"""T042 end to end on Postgres: draft, approve with a code, export, attribute, archive.

AS-2.3: a blocked creative cannot become an ad (409 CREATIVE_BLOCKED). AS-2.5: approving gives
the seller's next code and the wa.me link. FR-015: the export is a zip with the published image,
the copy and provenance.json. I-10: ``get_approved_asset`` is what a chat sends.
"""

import io
import json
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_catalog.sellers import PostgresSellerStore
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.providers import Blob
from vitrina_core.settings import Settings
from vitrina_core.storage import LocalBlobStore
from vitrina_creative.api import AdStore, get_approved_asset, read_provenance
from vitrina_creative.testing import source_photo

from .test_creative_db import SECRET, STRANGER, bearer, scripted_providers, sign_up

OWNER = "8d3b2e4f-5a6c-4b7d-9e0f-1a2b3c4d5e6f"
LINK_A1 = "https://wa.me/573000000701?text=Hola%2C%20vi%20el%20anuncio%20%23A1"


def owner() -> dict[str, str]:
    return bearer(OWNER)


@pytest.mark.db
async def test_db_as_2_5_an_ad_goes_from_draft_to_a_code_an_export_and_a_chat(
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
    storage = LocalBlobStore(tmp_path, public_url=settings.public_api_url)
    with TestClient(create_app(settings)) as client:
        seller_id = sign_up(client, OWNER, "+573000000701")
        sign_up(client, STRANGER, "+573000000702")
        product_id = _product_with_photo(client)
        started = client.post(
            "/v1/creative/jobs",
            json={"product_id": product_id, "formats": ["catalog_1x1"], "copy_variants": 6},
            headers=owner(),
        )
        assert started.status_code == 202, started.text
        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions),
            registry,
            sessions=db_sessions,
            providers=scripted_providers(),
            storage=storage,
            settings=settings,
        )
        assert await worker.run_once() is True
        job = client.get(f"/v1/creative/jobs/{started.json()['id']}", headers=owner()).json()
        passed, blocked, other = job["creatives"]
        assert blocked["verification"]["status"] == "blocked"
        first_copy, second_copy = job["copy_variants"][:2]

        def create(creative: dict[str, Any], copy: dict[str, Any]) -> Any:
            body = {"creative_id": creative["id"], "copy_variant_id": copy["id"]}
            return client.post("/v1/ads", json=body, headers=owner())

        refused = create(blocked, first_copy)
        assert (refused.status_code, refused.json()["code"]) == (409, "CREATIVE_BLOCKED")
        missing = client.post(
            "/v1/ads",
            json={"creative_id": passed["id"], "copy_variant_id": passed["id"]},
            headers=owner(),
        )
        assert missing.status_code == 404

        draft = create(passed, first_copy)
        assert draft.status_code == 201, draft.text
        ad = draft.json()
        assert (ad["status"], ad["code"], ad["wa_link"]) == ("draft", None, None)
        assert ad["angle"] == first_copy["angle"]
        early = client.get(f"/v1/ads/{ad['id']}/export", headers=owner())
        assert (early.status_code, early.json()["code"]) == (409, "CONFLICT")

        approved = client.post(f"/v1/ads/{ad['id']}/approve", headers=owner()).json()
        assert (approved["status"], approved["code"], approved["wa_link"]) == (
            "approved",
            "A1",
            LINK_A1,
        )
        assert approved["approved_at"] is not None
        again = client.post(f"/v1/ads/{ad['id']}/approve", headers=owner()).json()
        assert again["code"] == "A1"
        later = client.get(f"/v1/creative/jobs/{job['id']}", headers=owner()).json()
        assert later["copy_variants"][0]["status"] == "approved"

        second = create(other, second_copy).json()
        assert (
            client.post(f"/v1/ads/{second['id']}/approve", headers=owner()).json()["code"] == "A2"
        )
        assert client.get("/v1/ads/by-code/A2", headers=owner()).json()["id"] == second["id"]
        assert client.get("/v1/ads/by-code/A9", headers=owner()).status_code == 404
        listed = client.get("/v1/ads?status=approved", headers=owner()).json()["items"]
        assert [item["code"] for item in listed] == ["A2", "A1"]
        assert client.get("/v1/ads?status=draft", headers=owner()).json()["items"] == []

        export = client.get(f"/v1/ads/{ad['id']}/export", headers=owner())
        assert (export.status_code, export.headers["content-type"]) == (200, "application/zip")
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        assert set(files) == {"A1-catalog_1x1.png", "copy.txt", "provenance.json"}
        assert files["copy.txt"].decode().rstrip().endswith(LINK_A1)
        assert json.loads(files["provenance.json"])["ad"]["code"] == "A1"
        png = Blob(data=files["A1-catalog_1x1.png"], mime_type="image/png")
        assert read_provenance(png)["generator"] == "muse-test"

        store, sellers = AdStore(db_sessions), PostgresSellerStore(db_sessions)
        newest = await get_approved_asset(
            UUID(seller_id), UUID(product_id), store=store, storage=storage, sellers=sellers
        )
        chosen = await get_approved_asset(
            UUID(seller_id),
            UUID(product_id),
            store=store,
            storage=storage,
            sellers=sellers,
            ad_id=UUID(ad["id"]),
        )
        assert newest is not None
        assert chosen is not None
        assert (newest.code, chosen.code) == ("A2", "A1")
        assert chosen.caption.endswith(LINK_A1)

        assert client.delete(f"/v1/ads/{second['id']}", headers=owner()).status_code == 204
        kept = client.get("/v1/ads/by-code/A2", headers=owner()).json()
        assert kept["status"] == "archived"  # the code still attributes
        relaunch = client.post(f"/v1/ads/{second['id']}/approve", headers=owner())
        assert (relaunch.status_code, relaunch.json()["code"]) == (409, "CONFLICT")
        after = await get_approved_asset(
            UUID(seller_id), UUID(product_id), store=store, storage=storage, sellers=sellers
        )
        assert after is not None
        assert after.code == "A1"

        stranger = bearer(STRANGER)
        assert client.get(f"/v1/ads/{ad['id']}", headers=stranger).status_code == 404
        assert client.get("/v1/ads/by-code/A1", headers=stranger).status_code == 404
        assert client.post(f"/v1/ads/{ad['id']}/approve", headers=stranger).status_code == 404
        assert client.get(f"/v1/ads/{ad['id']}/export", headers=stranger).status_code == 404
        assert client.delete(f"/v1/ads/{ad['id']}", headers=stranger).status_code == 404
        assert client.get("/v1/ads", headers=stranger).json()["items"] == []


def _product_with_photo(client: TestClient) -> str:
    product_id = new_product_for(client)
    photo = client.post(
        "/v1/assets",
        files={"file": ("bolso.png", source_photo().image.data, "image/png")},
        data={"product_id": product_id},
        headers=owner(),
    )
    assert photo.status_code == 201, photo.text
    return product_id


def new_product_for(client: TestClient) -> str:
    response = client.post(
        "/v1/products",
        json={"name": "Bolso tejido a mano", "price_cop": 85000, "stock": 3},
        headers=owner(),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])
