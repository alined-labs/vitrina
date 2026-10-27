from uuid import UUID

import jwt
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_compliance.api import PostgresCompliance, record_consent
from vitrina_compliance.tables import erasure_receipts
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.settings import Settings

SECRET = "contacts-db-test-secret-with-32-bytes+"
SUBJECT = "4e1a7c2d-9b3f-4f6a-8c5e-1d2b3a4c5d6e"
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"


def bearer() -> dict[str, str]:
    claims = {"sub": SUBJECT, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.mark.db
async def test_db_as_6_6_borrar_mis_datos_runs_through_the_worker_and_leaves_a_receipt(
    migrated_schema: tuple[str, str], db_sessions: async_sessionmaker[AsyncSession]
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None,
        database_url=url,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        contact_hash_pepper="contacts-db-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
    )  # type: ignore[call-arg]
    app = create_app(settings)
    with TestClient(app) as client:
        signup = client.post(
            "/v1/sellers",
            json={
                "business_name": "Mochilas Ana",
                "owner_name": "Ana",
                "whatsapp_number": "+573000000301",
                "city": "Medellín",
            },
            headers=bearer(),
        )
        seller_id = UUID(signup.json()["id"])
        # The test's own sessions: the app's engine belongs to the TestClient's event loop.
        store = PostgresCompliance(db_sessions, app.state.compliance.identity)
        contact = await store.resolve_contact(seller_id, "573000000002", display_name="Laura")
        await record_consent(store, seller_id, contact.id, prompt_text=PROMPT, response_text="sí")

        accepted = client.delete(f"/v1/contacts/{contact.id}", headers=bearer())
        assert accepted.status_code == 202
        assert accepted.json()["kind"] == "erasure"

        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions), registry, sessions=db_sessions, settings=settings
        )
        assert await worker.run_once() is True

        job = client.get(f"/v1/jobs/{accepted.json()['id']}", headers=bearer()).json()
        erased = client.get(f"/v1/contacts/{contact.id}", headers=bearer()).json()
        ledger = client.get(f"/v1/contacts/{contact.id}/consents", headers=bearer()).json()

    assert (job["status"], job["result_ref"]) == ("succeeded", f"/v1/contacts/{contact.id}")
    assert erased["display_name"] is None
    assert "phone_masked" not in erased
    assert erased["erased_at"] is not None
    assert erased["opted_out_at"] is not None
    assert [entry["status"] for entry in ledger] == ["granted"]  # the ledger is never removed
    async with db_sessions() as session:
        receipts = (await session.execute(sa.select(erasure_receipts))).mappings().all()
    assert [(receipt["scope"], receipt["completed_at"] is not None) for receipt in receipts] == [
        ("contact", True)
    ]
