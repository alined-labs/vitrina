"""T034 end to end on Postgres: a creative job from the route through the worker (AS-2.1, AS-2.2).

The job is started over HTTP, the worker runs ``creative.run_job`` with scripted providers (a
perfect segmenter, scenes in a fixed order, Spark answering each purpose), and the routes then
show the creatives with their verification and the scored copy. The altered scene is stored
for audit and reported as blocked (FR-013).
"""

from pathlib import Path
from urllib.parse import urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.providers import Blob
from vitrina_core.providers.compose import NoLiveAdapter, ProviderMode, Providers
from vitrina_core.settings import Settings
from vitrina_core.storage import LocalBlobStore
from vitrina_creative.provenance import read_provenance
from vitrina_creative.testing import (
    MaskOracle,
    ScriptedMuse,
    SparkByPurpose,
    altered_scenes,
    good_scenes,
    source_photo,
)

SECRET = "creative-db-test-secret-with-32-bytes+"
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
        headers=bearer(subject),
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
