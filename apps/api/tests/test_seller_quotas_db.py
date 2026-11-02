"""Per-seller daily quotas on Postgres: creative jobs, menu imports, live replays and evals.

The counts survive a restart, one seller's count never touches another's, and work the quota
refuses queues nothing. ``test_agent_turns_routes`` and ``test_briefs_routes`` cover their routes.
"""

from pathlib import Path
from typing import Any

import jwt
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_core.db.tables import jobs
from vitrina_core.settings import Settings
from vitrina_creative.testing import source_photo

SECRET = "seller-quotas-db-test-secret-with-32-bytes"
OWNER = "6b1f0c2e-3d4a-4e5f-8a9b-0c1d2e3f4a5c"
STRANGER = "7c2a1d3f-4e5b-4f6a-9b0c-1d2e3f4a5b6d"
PNG = b"\x89PNG\r\n\x1a\n synthetic menu photo"
SERVICE = {"X-Service-Token": "svc-token"}
UNKNOWN_PRODUCT = "00000000-0000-7000-8000-0000000000ff"


def bearer(subject: str) -> dict[str, str]:
    claims = {"sub": subject, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


def settings_for(migrated_schema: tuple[str, str], tmp_path: Path, **limits: object) -> Settings:
    url, schema = migrated_schema
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=url,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        service_token="svc-token",
        storage_dir=tmp_path,
        model_agent="spark-test",
        contact_hash_pepper="seller-quotas-test-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
        **limits,
    )


def seller_with_a_photographed_product(client: TestClient, subject: str, number: str) -> str:
    signup = client.post(
        "/v1/sellers",
        json={
            "business_name": "Tejidos Vecina",
            "owner_name": "Ana",
            "whatsapp_number": number,
            "city": "Medellín",
        },
        headers=bearer(subject),
    )
    assert signup.status_code == 201, signup.text
    product = client.post(
        "/v1/products",
        json={"name": "Bolso tejido", "price_cop": 85000, "stock": 3},
        headers=bearer(subject),
    )
    assert product.status_code == 201, product.text
    product_id = str(product.json()["id"])
    photo = client.post(
        "/v1/assets",
        files={"file": ("bolso.png", source_photo().image.data, "image/png")},
        data={"product_id": product_id},
        headers=bearer(subject),
    )
    assert photo.status_code == 201, photo.text
    return product_id


def stored_photos(root: Path) -> list[Path]:
    return sorted(root.rglob("*.png"))


async def queued(db_sessions: async_sessionmaker[AsyncSession], kind: str) -> int:
    async with db_sessions() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(jobs).where(jobs.c.kind == kind)
        )
    return int(count or 0)


@pytest.mark.db
async def test_db_as_2_1_the_creative_job_quota_is_per_seller_and_survives_a_restart(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = settings_for(migrated_schema, tmp_path, seller_daily_creative_jobs=1)

    def start(client: TestClient, subject: str, product_id: str) -> Any:
        return client.post(
            "/v1/creative/jobs",
            json={"product_id": product_id, "formats": ["catalog_1x1"]},
            headers=bearer(subject),
        )

    with TestClient(create_app(settings)) as client:
        mine = seller_with_a_photographed_product(client, OWNER, "+573000000801")
        theirs = seller_with_a_photographed_product(client, STRANGER, "+573000000802")

        missing = start(client, OWNER, UNKNOWN_PRODUCT)
        assert missing.status_code == 404  # refused: place returned
        accepted = start(client, OWNER, mine)
        assert accepted.status_code == 202, accepted.text
        over = start(client, OWNER, mine)
        assert over.status_code == 429
        assert over.json()["code"] == "RATE_LIMITED"
        assert int(over.headers["Retry-After"]) > 0
        assert start(client, STRANGER, theirs).status_code == 202

    with TestClient(create_app(settings)) as restarted:
        assert start(restarted, OWNER, mine).status_code == 429

    assert await queued(db_sessions, "creative") == 2


@pytest.mark.db
async def test_db_as_1_3_a_menu_import_over_the_quota_stores_and_queues_nothing(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = settings_for(migrated_schema, tmp_path, seller_daily_paid_jobs=1)

    with TestClient(create_app(settings)) as client:
        seller_with_a_photographed_product(client, OWNER, "+573000000803")

        def send() -> Any:
            return client.post(
                "/v1/products/import",
                files={"image": ("menu.png", PNG, "image/png")},
                headers=bearer(OWNER),
            )

        accepted = send()
        assert accepted.status_code == 202, accepted.text
        stored = stored_photos(tmp_path)
        over = send()

    assert over.status_code == 429
    assert stored_photos(tmp_path) == stored
    assert await queued(db_sessions, "product_import") == 1


@pytest.mark.db
async def test_db_as_8_1_a_live_replay_counts_and_a_recorded_one_does_not(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = settings_for(migrated_schema, tmp_path, seller_daily_paid_jobs=0)
    await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000804",
            city="Medellín",
        ),
        auth_subject=OWNER,
    )

    with TestClient(create_app(settings)) as client:
        live = client.post(
            "/v1/replay/sessions",
            json={"fixture_id": "reserva-mochila"},
            headers=bearer(OWNER),
        )
        recorded = client.post(
            "/v1/replay/sessions",
            json={"fixture_id": "reserva-mochila", "use_recorded_model_outputs": True},
            headers=bearer(OWNER),
        )

    assert (live.status_code, live.json()["code"]) == (429, "RATE_LIMITED")
    assert recorded.status_code == 202, recorded.text
    assert await queued(db_sessions, "replay") == 1


@pytest.mark.db
async def test_db_as_7_1_only_a_sellers_live_eval_run_counts(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = settings_for(migrated_schema, tmp_path, seller_daily_paid_jobs=0)
    live = {"suite": "conversations_es", "replay": False}
    recorded = {"suite": "conversations_es", "replay": True}

    with TestClient(create_app(settings)) as client:
        seller_with_a_photographed_product(client, OWNER, "+573000000805")
        seller_live = client.post("/v1/evals/runs", json=live, headers=bearer(OWNER))
        seller_recorded = client.post("/v1/evals/runs", json=recorded, headers=bearer(OWNER))
        worker_live = client.post("/v1/evals/runs", json=live, headers=SERVICE)

    assert (seller_live.status_code, seller_live.json()["code"]) == (429, "RATE_LIMITED")
    assert seller_recorded.status_code == 202, seller_recorded.text
    assert worker_live.status_code == 202, worker_live.text
    assert await queued(db_sessions, "eval_run") == 2
