"""T080 end to end on Postgres (CI's db job): a replay session is a ``replay`` job that the
worker plays into a fresh replay conversation, through the local mock (AS-8.1, FR-071).
"""

import dataclasses
from pathlib import Path

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_conversation.api import PostgresConversationStore
from vitrina_conversation.replay import replay_fixture, start_replay_session
from vitrina_conversation.tables import conversations, messages
from vitrina_conversation.testing import ScriptedChat, final_answer
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_local_mock import MockOutbox

pytestmark = pytest.mark.db

GREETING = "¡Hola! ¿En qué te ayudo?"


async def test_db_as_8_1_a_replay_session_plays_through_the_worker(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    pepper, key = "replay-db-test-pepper", Fernet.generate_key().decode()
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        model_agent="spark-test",
        mock_outbox_path=tmp_path / "mock-whatsapp.jsonl",
        contact_hash_pepper=pepper,
        contact_encryption_key=key,
    )
    seller = await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000101",
            city="Medellín",
        ),
        auth_subject=None,
    )
    fixture = replay_fixture("reserva-mochila")
    assert fixture is not None
    identity = ContactIdentity(pepper=SecretStr(pepper), key=SecretStr(key))

    session = await start_replay_session(
        sessions=db_sessions,
        store=PostgresConversationStore(db_sessions),
        compliance=PostgresCompliance(db_sessions, identity),
        seller_id=seller.id,
        fixture=fixture,
        speed=10,
    )

    providers = dataclasses.replace(
        compose(settings, InMemoryLedger(), mode=ProviderMode.replay),
        chat=ScriptedChat([final_answer(GREETING, confidence=0.95)] * 3),
    )
    registry = TaskRegistry()
    load_tasks(registry)
    worker = Worker(
        PostgresJobQueue(db_sessions),
        registry,
        sessions=db_sessions,
        providers=providers,
        settings=settings,
    )
    assert await worker.run_once() is True

    outbox = MockOutbox(settings.mock_outbox_path)
    assert [entry.text for entry in outbox.entries(contact="+573000000012")] == [GREETING] * 3
    async with db_sessions() as db:
        replay = (
            await db.execute(
                sa.select(conversations.c.is_replay).where(
                    conversations.c.id == session.conversation_id
                )
            )
        ).scalar_one()
        rows = (
            await db.execute(
                sa.select(messages.c.direction, messages.c.is_replay, messages.c.wa_message_id)
                .where(messages.c.conversation_id == session.conversation_id)
                .order_by(messages.c.created_at, messages.c.id)
            )
        ).all()
    assert replay is True
    assert [row.direction for row in rows] == ["inbound", "outbound"] * 3
    assert all(row.is_replay for row in rows)
    inbound_ids = [row.wa_message_id for row in rows if row.direction == "inbound"]
    assert inbound_ids == [f"{m.message_id}.{session.session_id}" for m in fixture.messages]
