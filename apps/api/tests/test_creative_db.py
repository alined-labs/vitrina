"""T034 end to end on Postgres: a creative job from the route through the worker (AS-2.1, AS-2.2).

The job is started over HTTP, the worker runs ``creative.run_job`` with scripted providers (a
perfect segmenter, scenes in a fixed order, Spark answering each purpose), and the routes then
show the creatives with their verification and the scored copy. The altered scene is stored
for audit and reported as blocked (FR-013).
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_core.db.tables import jobs
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.providers import Blob, ImageRequest, ImageResponse, ProviderError
from vitrina_core.providers.compose import NoLiveAdapter, ProviderMode, Providers
from vitrina_core.settings import Settings
from vitrina_core.storage import LocalBlobStore
from vitrina_creative.copy import NO_ANGLE_LEFT
from vitrina_creative.jobs import JOB_GAVE_UP
from vitrina_creative.provenance import read_provenance
from vitrina_creative.reconcile import reconcile_failed_jobs
from vitrina_creative.tasks import CREATIVE_TASK, creative_gave_up, run_creative_job
from vitrina_creative.testing import (
    MaskOracle,
    ScriptedMuse,
    SparkByPurpose,
    altered_scenes,
    good_scenes,
    source_photo,
)

SECRET = "creative-db-test-secret-with-32-bytes+"
SERVICE_TOKEN = "service-token-for-tests"
OWNER = "6b1f0c2e-3d4a-4e5f-8a9b-0c1d2e3f4a5b"
STRANGER = "7c2a1d3f-4e5b-4f6a-9b0c-1d2e3f4a5b6c"
BLUE = next(scene for scene in altered_scenes() if scene.name == "blue")


def bearer(subject: str) -> dict[str, str]:
    claims = {"sub": subject, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


def sign_up(client: TestClient, subject: str, number: str) -> str:
    response = client.post(
        "/v1/sellers",
        json={
            "business_name": "Tejidos Vecina",
            "owner_name": "Ana",
            "whatsapp_number": number,
            "city": "Medellín",
        },
        headers={**bearer(subject), "X-Service-Token": SERVICE_TOKEN},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def new_product(client: TestClient, name: str) -> str:
    response = client.post(
        "/v1/products",
        json={"name": name, "price_cop": 85000, "stock": 3, "description": "Hilo de algodón."},
        headers=bearer(OWNER),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def scripted_providers() -> Providers:
    scenes = (good_scenes()[0], BLUE, good_scenes()[1])
    return Providers(
        mode=ProviderMode.replay,
        chat=SparkByPurpose(),
        embedder=NoLiveAdapter("embed"),
        image=ScriptedMuse(*scenes),
        segmenter=MaskOracle(source_photo(), *scenes),
        transcriber=NoLiveAdapter("transcribe"),
    )


class RefusingMuse:
    """Muse Image answering a 4xx: a final failure on the job's first attempt."""

    async def generate(self, request: ImageRequest) -> ImageResponse:
        raise ProviderError("400 from images", provider="meta_model_api", status_code=400)


class HangingMuse:
    """Muse Image that never answers, so the job reaches its timeout."""

    async def generate(self, request: ImageRequest) -> ImageResponse:
        await asyncio.sleep(30)
        raise AssertionError("the job should have timed out")


def providers_with(image: Any) -> Providers:
    return Providers(
        mode=ProviderMode.replay,
        chat=SparkByPurpose(),
        embedder=NoLiveAdapter("embed"),
        image=image,
        segmenter=MaskOracle(source_photo(), *good_scenes()),
        transcriber=NoLiveAdapter("transcribe"),
    )


def creative_settings(url: str, schema: str, storage_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=url,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        service_token=SERVICE_TOKEN,
        storage_dir=storage_dir,
        model_agent="spark-test",
        model_image="muse-test",
        model_segment="sam-test",
    )  # type: ignore[call-arg]


def started_job(
    client: TestClient, number: str, name: str = "Bolso tejido a mano", **params: Any
) -> str:
    """A seller, a product with one photo and its queued creative job; returns the job's id."""
    sign_up(client, OWNER, number)
    product_id = new_product(client, name)
    photo = client.post(
        "/v1/assets",
        files={"file": ("bolso.png", source_photo().image.data, "image/png")},
        data={"product_id": product_id},
        headers=bearer(OWNER),
    )
    assert photo.status_code == 201, photo.text
    accepted = client.post(
        "/v1/creative/jobs", json={"product_id": product_id, **params}, headers=bearer(OWNER)
    )
    assert accepted.status_code == 202, accepted.text
    return str(accepted.json()["id"])


def creative_worker(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    storage_dir: Path,
    providers: Providers,
    registry: TaskRegistry | None = None,
) -> Worker:
    if registry is None:
        registry = TaskRegistry()
        load_tasks(registry)
    return Worker(
        PostgresJobQueue(sessions),
        registry,
        sessions=sessions,
        providers=providers,
        storage=LocalBlobStore(storage_dir, public_url=settings.public_api_url),
        settings=settings,
    )


async def on_last_attempt(sessions: async_sessionmaker[AsyncSession], job_id: str) -> None:
    """The next claim is the job's last attempt, and it is due now."""
    async with sessions() as session, session.begin():
        await session.execute(
            sa.update(jobs)
            .where(jobs.c.id == UUID(job_id))
            .values(attempts=jobs.c.max_attempts - 1, run_after=sa.func.now())
        )


# What the queue records for a job whose worker was killed on its last attempt.
STALLED = {"code": "STALLED", "message": "no heartbeat", "retryable": False}


@pytest.mark.db
async def test_db_as_2_1_a_creative_job_runs_from_the_route_through_the_worker(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None,
        database_url=url,
        service_token=SERVICE_TOKEN,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        storage_dir=tmp_path,
        model_agent="spark-test",
        model_image="muse-test",
        model_segment="sam-test",
    )  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        sign_up(client, OWNER, "+573000000501")
        sign_up(client, STRANGER, "+573000000502")
        product_id = new_product(client, "Bolso tejido a mano")
        bare_id = new_product(client, "Bolso sin fotos")
        photo = client.post(
            "/v1/assets",
            files={"file": ("bolso.png", source_photo().image.data, "image/png")},
            data={"product_id": product_id},
            headers=bearer(OWNER),
        )
        assert photo.status_code == 201, photo.text

        def start(body: dict[str, object], subject: str = OWNER):  # type: ignore[no-untyped-def]
            return client.post("/v1/creative/jobs", json=body, headers=bearer(subject))

        params = {"formats": ["catalog_1x1"], "scenes_per_format": 3, "copy_variants": 6}
        no_photos = start({"product_id": bare_id, **params})
        not_its_photo = start({"product_id": bare_id, "asset_ids": [photo.json()["id"]]})
        unknown = start({"product_id": "00000000-0000-7000-8000-0000000000ff"})
        too_many = start({"product_id": product_id, "scenes_per_format": 9})
        strangers = start({"product_id": product_id}, STRANGER)
        assert (no_photos.status_code, no_photos.json()["errors"][0]["field"]) == (400, "asset_ids")
        assert (not_its_photo.status_code, not_its_photo.json()["errors"][0]["field"]) == (
            400,
            "asset_ids",
        )
        assert (unknown.status_code, strangers.status_code) == (404, 404)
        assert too_many.status_code == 400

        accepted = start({"product_id": product_id, **params})
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["id"]
        assert (accepted.json()["status"], accepted.json()["stage"]) == ("queued", "queued")

        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions),
            registry,
            sessions=db_sessions,
            providers=scripted_providers(),
            storage=LocalBlobStore(tmp_path, public_url=settings.public_api_url),
            settings=settings,
        )
        assert await worker.run_once() is True

        job = client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert (job["status"], job["stage"], job["product_id"]) == (
            "succeeded",
            "ready",
            product_id,
        )
        statuses = [creative["verification"]["status"] for creative in job["creatives"]]
        assert statuses == ["passed", "blocked", "passed"]
        assert {creative["source_asset_id"] for creative in job["creatives"]} == {
            photo.json()["id"]
        }
        assert job["creatives"][0]["provenance"]["generator"] == "muse-test"
        assert len(job["copy_variants"]) == 6
        assert {variant["status"] for variant in job["copy_variants"]} == {"candidate"}
        assert {variant["brand_voice_score"] for variant in job["copy_variants"]} == {4.0}

        blocked_id = job["creatives"][1]["id"]
        blocked = client.get(f"/v1/creatives/{blocked_id}", headers=bearer(OWNER)).json()
        assert blocked["verification"]["reason"] == "color del producto alterado — bloqueado"
        link = urlsplit(blocked["image_url"])
        image = client.get(f"{link.path}?{link.query}")
        assert (image.status_code, image.headers["content-type"]) == (200, "image/png")
        assert (
            read_provenance(Blob(data=image.content, mime_type="image/png"))["verification"]
            == "blocked"
        )

        mine = client.get(f"/v1/creative/jobs?product_id={product_id}", headers=bearer(OWNER))
        other = client.get(f"/v1/creative/jobs?product_id={bare_id}", headers=bearer(OWNER))
        assert [item["id"] for item in mine.json()["items"]] == [job_id]
        assert other.json()["items"] == []

        assert (
            client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(STRANGER)).status_code == 404
        )
        assert (
            client.get(f"/v1/creatives/{blocked_id}", headers=bearer(STRANGER)).status_code == 404
        )
        assert client.get("/v1/creative/jobs", headers=bearer(STRANGER)).json()["items"] == []


@pytest.mark.db
async def test_db_a_photo_without_the_product_fails_the_job_with_a_reason(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None,
        database_url=url,
        service_token=SERVICE_TOKEN,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        storage_dir=tmp_path,
        model_agent="spark-test",
        model_image="muse-test",
        model_segment="sam-test",
    )  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        sign_up(client, OWNER, "+573000000503")
        product_id = new_product(client, "Bolso tejido a mano")
        photo = client.post(
            "/v1/assets",
            files={"file": ("bolso.png", source_photo().image.data, "image/png")},
            data={"product_id": product_id},
            headers=bearer(OWNER),
        )
        assert photo.status_code == 201, photo.text
        accepted = client.post(
            "/v1/creative/jobs", json={"product_id": product_id}, headers=bearer(OWNER)
        )
        assert accepted.status_code == 202, accepted.text

        blind = Providers(
            mode=ProviderMode.replay,
            chat=SparkByPurpose(),
            embedder=NoLiveAdapter("embed"),
            image=ScriptedMuse(good_scenes()[0]),
            segmenter=MaskOracle(),  # finds no product in any photo
            transcriber=NoLiveAdapter("transcribe"),
        )
        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions),
            registry,
            sessions=db_sessions,
            providers=blind,
            storage=LocalBlobStore(tmp_path, public_url=settings.public_api_url),
            settings=settings,
        )
        assert await worker.run_once() is True

        job = client.get(f"/v1/creative/jobs/{accepted.json()['id']}", headers=bearer(OWNER))
        body = job.json()
        assert (body["status"], body["stage"]) == ("failed", "failed")
        assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
        assert body["creatives"] == []
        assert body["copy_variants"] == []


@pytest.mark.db
async def test_db_as_2_1_an_image_model_refusal_fails_the_job_on_its_first_attempt(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = creative_settings(*migrated_schema, tmp_path)
    with TestClient(create_app(settings)) as client:
        job_id = started_job(client, "+573000000504")

        worker = creative_worker(db_sessions, settings, tmp_path, providers_with(RefusingMuse()))
        assert await worker.run_once() is True

        body = client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert (body["status"], body["stage"]) == ("failed", "failed")
        assert (body["error"]["code"], body["error"]["message"]) == ("PROVIDER_ERROR", JOB_GAVE_UP)
        assert body["finished_at"] is not None
        queued = client.get(f"/v1/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert queued["status"] == "failed"


@pytest.mark.db
async def test_db_as_2_1_a_timeout_waits_for_the_last_attempt_and_then_fails_the_job(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = creative_settings(*migrated_schema, tmp_path)
    registry = TaskRegistry()
    registry.add(CREATIVE_TASK, run_creative_job, timeout_s=1.0, on_final_failure=creative_gave_up)
    with TestClient(create_app(settings)) as client:
        job_id = started_job(client, "+573000000505")
        worker = creative_worker(
            db_sessions, settings, tmp_path, providers_with(HangingMuse()), registry
        )

        assert await worker.run_once() is True
        retrying = client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert retrying["status"] in {"queued", "running"}
        assert client.get(f"/v1/jobs/{job_id}", headers=bearer(OWNER)).json()["status"] == "queued"

        await on_last_attempt(db_sessions, job_id)
        assert await worker.run_once() is True

        body = client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert (body["status"], body["stage"]) == ("failed", "failed")
        assert (body["error"]["code"], body["error"]["message"]) == ("TIMEOUT", JOB_GAVE_UP)


@pytest.mark.db
async def test_db_as_2_1_the_sweep_closes_a_killed_job_and_never_a_running_one(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = creative_settings(*migrated_schema, tmp_path)
    queue = PostgresJobQueue(db_sessions)
    now = datetime.now(UTC)
    with TestClient(create_app(settings)) as client:
        job_id = started_job(client, "+573000000506")

        def row() -> dict[str, Any]:
            found: dict[str, Any] = client.get(
                f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)
            ).json()
            return found

        assert await reconcile_failed_jobs(db_sessions, now) == 0  # queued
        await queue.claim("w1", frozenset({CREATIVE_TASK}))
        assert await reconcile_failed_jobs(db_sessions, now) == 0  # running

        await queue.fail(UUID(job_id), {**STALLED, "retryable": True}, now)
        assert await reconcile_failed_jobs(db_sessions, now) == 0  # back in the queue for a retry
        assert row()["status"] == "queued"

        await on_last_attempt(db_sessions, job_id)
        await queue.claim("w2", frozenset({CREATIVE_TASK}))
        assert await reconcile_failed_jobs(db_sessions, now) == 0  # running its last attempt
        assert row()["status"] == "queued"

        await queue.fail(UUID(job_id), STALLED, None)
        assert await reconcile_failed_jobs(db_sessions, now) == 1

        body = row()
        assert (body["status"], body["stage"]) == ("failed", "failed")
        assert (body["error"]["code"], body["error"]["message"]) == ("STALLED", JOB_GAVE_UP)
        assert await reconcile_failed_jobs(db_sessions, now) == 0  # nothing left to close


@pytest.mark.db
async def test_db_as_2_2_the_sweep_leaves_a_finished_job_as_it_is(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = creative_settings(*migrated_schema, tmp_path)
    with TestClient(create_app(settings)) as client:
        job_id = started_job(
            client, "+573000000507", formats=["catalog_1x1"], scenes_per_format=3, copy_variants=6
        )
        worker = creative_worker(db_sessions, settings, tmp_path, scripted_providers())
        assert await worker.run_once() is True
        # The results were saved and then the queue lost track of the job (its worker died).
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.update(jobs).where(jobs.c.id == UUID(job_id)).values(status="failed")
            )

        assert await reconcile_failed_jobs(db_sessions, datetime.now(UTC)) == 0

        body = client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert (body["status"], body["stage"], body["error"]) == ("succeeded", "ready", None)
        assert len(body["creatives"]) == 3


@pytest.mark.db
async def test_db_as_2_4_a_job_the_product_cannot_write_copy_for_fails_before_any_paid_call(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = creative_settings(*migrated_schema, tmp_path)
    # Any paid call would fail with another code than the one this test expects.
    unpaid = Providers(
        mode=ProviderMode.replay,
        chat=NoLiveAdapter("chat"),
        embedder=NoLiveAdapter("embed"),
        image=NoLiveAdapter("image"),
        segmenter=NoLiveAdapter("segment"),
        transcriber=NoLiveAdapter("transcribe"),
    )
    with TestClient(create_app(settings)) as client:
        job_id = started_job(client, "+573000000508", "Camiseta básica", angles=["handmade"])

        worker = creative_worker(db_sessions, settings, tmp_path, unpaid)
        assert await worker.run_once() is True

        body = client.get(f"/v1/creative/jobs/{job_id}", headers=bearer(OWNER)).json()
        assert (body["status"], body["stage"]) == ("failed", "failed")
        assert (body["error"]["code"], body["error"]["message"]) == (
            "VALIDATION_ERROR",
            NO_ANGLE_LEFT,
        )
