"""T064 end to end on Postgres (AS-6.2, AS-6.7, FR-064; CI's db job): a preview over the
seller's quiet contacts, 409 until the marketing template is approved, then the
``campaign_send`` job through the worker, and one contact's eligibility on demand.
"""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import jwt
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_compliance.api import PostgresCompliance, record_consent
from vitrina_compliance.calendar import BOGOTA
from vitrina_conversation.api import PostgresConversationStore
from vitrina_conversation.tables import messages
from vitrina_core.db.tables import jobs, sellers
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.settings import Settings

SECRET = "reactivation-db-test-secret-with-32-bytes"
SUBJECT = "6b2f0c1e-3d4a-4e5b-8f6a-7b8c9d0e1f2a"
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"
NEXT_WEDNESDAY_10 = datetime(2026, 11, 4, 10, 0, tzinfo=BOGOTA)


def bearer() -> dict[str, str]:
    claims = {"sub": SUBJECT, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.mark.db
async def test_db_as_6_7_a_campaign_from_preview_to_the_worker(
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
        contact_hash_pepper="reactivation-db-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
        mock_outbox_path=tmp_path / "outbox.jsonl",
    )  # type: ignore[call-arg]
    app = create_app(settings)
    with TestClient(app) as client:
        signup = client.post(
            "/v1/sellers",
            json={
                "business_name": "Mochilas Ana",
                "owner_name": "Ana",
                "whatsapp_number": "+573000000311",
                "city": "Medellín",
            },
            headers=bearer(),
        )
        seller_id = UUID(signup.json()["id"])
        compliance = PostgresCompliance(db_sessions, app.state.compliance.identity)
        store = PostgresConversationStore(db_sessions)
        contacts = {}
        for number, consent in (("573000000011", True), ("573000000012", False)):
            person = await compliance.resolve_contact(seller_id, number)
            if consent:
                await record_consent(
                    compliance, seller_id, person.id, prompt_text=PROMPT, response_text="sí"
                )
            chat = await store.open_conversation(seller_id, person.id)
            said = await store.add_message(
                seller_id,
                chat.id,
                direction="inbound",
                sender="customer",
                type="text",
                text="¿Hacen envíos?",
                wa_message_id=f"wamid.VITRINA-REACTIVATION-{number}",
            )
            assert said is not None
            async with db_sessions() as session, session.begin():
                await session.execute(
                    sa.update(messages)
                    .where(messages.c.id == said.id)
                    .values(created_at=sa.func.now() - timedelta(days=5))
                )
            contacts[consent] = person.id

        created = client.post(
            "/v1/reactivation/campaigns",
            json={
                "template_name": "vitrina_reactivacion",
                "scheduled_for": NEXT_WEDNESDAY_10.isoformat(),
            },
            headers=bearer(),
        )
        assert created.status_code == 201, created.text
        campaign = created.json()
        decisions = {UUID(d["contact_id"]): d["reason"] for d in campaign["decisions"]}
        assert decisions == {
            contacts[True]: "NO_MATCHING_AD",  # no approved ad for what they asked about
            contacts[False]: "NO_CONSENT_LEY_1581",
        }
        assert campaign["status"] == "preview"

        fetched = client.get(f"/v1/reactivation/campaigns/{campaign['id']}", headers=bearer())
        listed = client.get("/v1/reactivation/campaigns", headers=bearer()).json()
        missing = client.get(f"/v1/reactivation/campaigns/{contacts[True]}", headers=bearer())
        assert fetched.json()["counts"] == campaign["counts"]
        assert [item["id"] for item in listed["items"]] == [campaign["id"]]
        assert missing.status_code == 404

        refused = client.post(f"/v1/reactivation/campaigns/{campaign['id']}/send", headers=bearer())
        assert (refused.status_code, refused.json()["code"]) == (409, "TEMPLATE_NOT_APPROVED")
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.update(sellers)
                .where(sellers.c.id == seller_id)
                .values(marketing_template_status="approved")
            )
        accepted = client.post(
            f"/v1/reactivation/campaigns/{campaign['id']}/send", headers=bearer()
        )
        again = client.post(f"/v1/reactivation/campaigns/{campaign['id']}/send", headers=bearer())
        assert (accepted.status_code, accepted.json()["status"]) == (202, "scheduled")
        assert (again.status_code, again.json()["code"]) == (409, "CAMPAIGN_ALREADY_SENT")

        async with db_sessions() as session, session.begin():
            queued = (
                await session.execute(
                    sa.select(jobs.c.kind, jobs.c.run_after).where(jobs.c.kind == "campaign_send")
                )
            ).one()
            assert queued.run_after == NEXT_WEDNESDAY_10
            await session.execute(
                sa.update(jobs)
                .where(jobs.c.kind == "campaign_send")
                .values(run_after=sa.func.now())
            )
        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions), registry, sessions=db_sessions, settings=settings
        )
        assert await worker.run_once() is True
        done = client.get(f"/v1/reactivation/campaigns/{campaign['id']}", headers=bearer()).json()
        assert done["status"] == "sent"
        assert done["counts"]["sent"] == 0  # nobody was allowed: nothing went out

        decision = client.post(
            "/v1/reactivation/eligibility",
            json={"contact_id": str(contacts[True]), "at": NEXT_WEDNESDAY_10.isoformat()},
            headers=bearer(),
        )
        unknown = client.post(
            "/v1/reactivation/eligibility", json={"contact_id": campaign["id"]}, headers=bearer()
        )
    assert (decision.status_code, decision.json()["reason"]) == (200, "NO_MATCHING_AD")
    assert unknown.status_code == 404
    assert not (tmp_path / "outbox.jsonl").exists()
