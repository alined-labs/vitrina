"""T083 end to end on Postgres: Foto → Vitrina from upload to the public page (AS-8.2, FR-072)."""

import dataclasses
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_core.ids import NIL
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.providers import Blob
from vitrina_core.settings import Settings
from vitrina_core.storage import BlobNotFoundError, LocalBlobStore, blob_key
from vitrina_creative.api import read_provenance
from vitrina_creative.testing import SparkByPurpose, source_photo

from .test_creative_db import scripted_providers

SLUG = re.compile(r"^bolso-tejido-a-mano-[0-9a-f]{6}$")


@pytest.mark.db
async def test_db_as_8_2_a_photo_becomes_a_public_page_and_the_upload_is_deleted(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None,
        database_url=url,
        db_schema=schema,
        storage_dir=tmp_path,
        model_agent="spark-test",
        model_image="muse-test",
        model_segment="sam-test",
    )  # type: ignore[call-arg]
    storage = LocalBlobStore(tmp_path, public_url=settings.public_api_url)
    app = create_app(settings)  # the upload check asks the scripted Spark, which says yes
    app.state.providers = dataclasses.replace(app.state.providers, chat=SparkByPurpose())
    with TestClient(app) as client:

        def upload() -> Any:
            return client.post(
                "/v1/public/showcases",
                files={"image": ("bolso.png", source_photo().image.data, "image/png")},
                data={
                    "product_name": "Bolso tejido a mano",
                    "price_cop": "85000",
                    "city": "Medellín",
                },
            )

        created = upload()
        assert created.status_code == 202, created.text
        slug = created.json()["slug"]
        assert SLUG.fullmatch(slug)
        assert created.json()["page_url"].endswith(f"/v/{slug}")
        waiting = client.get(f"/v1/public/showcases/{slug}").json()
        assert (waiting["status"], waiting["scenes"], waiting["copies"]) == ("processing", [], [])

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

        page = client.get(f"/v1/public/showcases/{slug}").json()
        assert page["status"] == "ready"
        assert page["ai_label"] == "Imagen generada con IA"
        assert page["cta_url"].endswith("/login")
        assert [scene["format"] for scene in page["scenes"]] == [
            "story_9x16",
            "catalog_1x1",
            "feed_4x5",
        ]
        assert [scene["verification_status"] for scene in page["scenes"]] == [
            "passed",
            "blocked",
            "passed",
        ]
        assert "image_url" not in page["scenes"][1]  # a blocked scene never reaches a public page
        assert len(page["copies"]) == 10
        assert all("{wa_link}" not in copy for copy in page["copies"])

        link = urlsplit(page["scenes"][0]["image_url"])
        image = client.get(f"{link.path}?{link.query}")
        assert image.status_code == 200
        assert (
            read_provenance(Blob(data=image.content, mime_type="image/png"))["generator"]
            == "muse-test"
        )
        with pytest.raises(BlobNotFoundError):
            await storage.get(blob_key(NIL, "showcases", f"{slug}/source", "png"))

        assert [upload().status_code for _ in range(4)] == [202] * 4
        limited = upload()
        assert (limited.status_code, limited.json()["code"]) == (429, "RATE_LIMITED")
        assert int(limited.headers["Retry-After"]) > 0
        assert client.get("/v1/public/showcases/no-such-page-0").status_code == 404
