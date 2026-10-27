import json
from pathlib import Path
from uuid import UUID

import jwt
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_core.db.tables import jobs
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import CallMeta, ChatMessage, ChatRequest, ChatResponse
from vitrina_core.providers.compose import LiveProviders, ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_core.storage import seller_of

SECRET = "catalog-import-db-test-secret-32-bytes+"
SUBJECT = "0b9e4f8a-55c1-4c3e-8f0e-6a1d2c3b4a5f"
PNG = b"\x89PNG\r\n\x1a\n synthetic menu photo"
ANSWER = {
    "products": [
        {"name": "Bandeja paisa", "price_cop": 25000, "description": "Con chicharrón"},
        {"name": "Limonada de coco", "price_cop": "$9.000", "description": ""},
        {"name": "Sin precio", "price_cop": None, "description": ""},
    ]
}


class FakeSpark:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            message=ChatMessage(role="assistant", content=json.dumps(ANSWER)),
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose=request.purpose),
        )


def bearer() -> dict[str, str]:
    claims = {"sub": SUBJECT, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.mark.db
async def test_db_as_1_3_a_menu_photo_becomes_drafts_through_the_worker(
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
        model_agent="model-from-settings",
    )  # type: ignore[call-arg]
    app = create_app(settings)
    spark = FakeSpark()
    with TestClient(app) as client:
        signup = client.post(
            "/v1/sellers",
            json={
                "business_name": "Restaurante Ejemplo",
                "owner_name": "Ana",
                "whatsapp_number": "+573000000003",
                "city": "Medellín",
            },
            headers=bearer(),
        )
        seller_id = UUID(signup.json()["id"])
        accepted = client.post(
            "/v1/products/import",
            files={"image": ("menu.png", PNG, "image/png")},
            data={"hint": "menú de almuerzos"},
            headers=bearer(),
        )
        assert accepted.status_code == 202
        job = accepted.json()
        assert (job["kind"], job["status"]) == ("product_import", "queued")
        async with db_sessions() as session:
            payload = await session.scalar(sa.select(jobs.c.payload).where(jobs.c.id == job["id"]))
        assert payload["hint"] == "menú de almuerzos"
        assert seller_of(payload["image_key"]) == seller_id  # Art. V.3

        registry = TaskRegistry()
        load_tasks(registry)
        providers = compose(
            settings, InMemoryLedger(), lambda: LiveProviders(chat=spark), mode=ProviderMode.live
        )
        worker = Worker(
            PostgresJobQueue(db_sessions),
            registry,
            sessions=db_sessions,
            providers=providers,
            storage=app.state.storage,
            settings=settings,
        )
        assert await worker.run_once() is True

        polled = client.get(f"/v1/jobs/{job['id']}", headers=bearer()).json()
        drafts = client.get("/v1/products?status=draft", headers=bearer()).json()["items"]
        active = client.get("/v1/products?status=active", headers=bearer()).json()["items"]

    assert (polled["status"], polled["result_ref"]) == ("succeeded", "/v1/products?status=draft")
    assert sorted((d["name"], d["price_cop"], d["source"]) for d in drafts) == [
        ("Bandeja paisa", 25000, "image_import"),
        ("Limonada de coco", 9000, "image_import"),
    ]
    assert active == []
    [request] = spark.requests
    content = request.messages[1].content
    assert isinstance(content, tuple)
    assert any(getattr(part, "image", None) is not None for part in content)
    # FR-003: the agent's search never returns a draft.
    assert await PostgresCatalog(db_sessions).search(seller_id, "bandeja paisa") == []
