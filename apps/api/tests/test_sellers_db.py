from uuid import UUID

import jwt
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_catalog.sellers import PostgresSellerStore
from vitrina_core.db.tables import jobs
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.settings import Settings

SECRET = "sellers-db-test-secret-with-32-bytes-plus"
SUBJECT = "6f1c2a64-3c2e-4d55-9a1c-5c0d1e2f3a4b"
EXPORT = """27/10/26, 14:05 - Ana Bolsos: Hola veci! ¿Cómo vas? 😊
27/10/26, 14:06 - Cliente Uno: Buenas, ¿cuánto vale? mi número es 3000000009
27/10/26, 14:07 - Ana Bolsos: Ese bolsito vale $85.000, escríbeme al 300 000 0002
"""


def bearer() -> dict[str, str]:
    claims = {"sub": SUBJECT, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.mark.db
async def test_db_as_1_5_an_export_becomes_the_sellers_voice_through_the_worker(
    migrated_schema: tuple[str, str], db_sessions: async_sessionmaker[AsyncSession]
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None, database_url=url, db_schema=schema, supabase_jwt_secret=SECRET
    )  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        signup = client.post(
            "/v1/sellers",
            json={
                "business_name": "Bolsos Ana",
                "owner_name": "Ana",
                "whatsapp_number": "+573000000001",
                "city": "Medellín",
            },
            headers=bearer(),
        )
        assert signup.status_code == 201
        settings_row = await PostgresSellerStore(db_sessions).settings(UUID(signup.json()["id"]))
        assert settings_row is not None
        assert (settings_row.owner_name, settings_row.low_confidence_threshold) == ("Ana", 0.6)
        accepted = client.post(
            "/v1/sellers/me/voice-samples",
            files={"file": ("chat.txt", EXPORT.encode())},
            data={"seller_display_name": "Ana Bolsos"},
            headers=bearer(),
        )
        assert accepted.status_code == 202
        job = accepted.json()
        assert (job["kind"], job["status"]) == ("voice_import", "queued")

        async with db_sessions() as session:
            stored = await session.scalar(sa.select(jobs.c.payload).where(jobs.c.id == job["id"]))
        assert stored == {
            "lines": [
                "Hola veci! ¿Cómo vas? 😊",
                "Ese bolsito vale $85.000, escríbeme al [teléfono]",
            ]
        }

        registry = TaskRegistry()
        load_tasks(registry)
        assert (
            await Worker(PostgresJobQueue(db_sessions), registry, sessions=db_sessions).run_once()
            is True
        )

        polled = client.get(f"/v1/jobs/{job['id']}", headers=bearer()).json()
        profile = client.get("/v1/sellers/me", headers=bearer()).json()["brand_voice_profile"]
    assert (polled["status"], polled["result_ref"]) == ("succeeded", "/v1/sellers/me")
    assert profile["sample_count"] == 2
    assert "veci" in profile["regionalisms"]
    assert profile["emoji_rate"] == 0.5
