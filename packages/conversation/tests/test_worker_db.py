"""T027 end to end on Postgres: the webhook ingest queues, the worker answers (CI's db job).

The real job queue, worker and Postgres stores; the scripted Spark and the local mock
messenger stand in for the providers.
"""

import dataclasses
from pathlib import Path

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_conversation.api import (
    Ingest,
    PostgresConversationStore,
    PostgresInboundQueue,
    sellers_on_postgres,
)
from vitrina_conversation.tables import messages
from vitrina_conversation.tasks import _MESSENGERS, _messenger
from vitrina_conversation.testing import ScriptedChat, final_answer, webhook_fixture
from vitrina_core.db.tables import jobs as jobs_table
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import Messenger, OutboundMessage, RateLimitedError, SendResult
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_local_mock import MockOutbox
from vitrina_whatsapp.signature import sign

pytestmark = pytest.mark.db

SECRET = "worker-db-test-app-secret"
GREETING = "¡Hola! ¿En qué te ayudo?"


async def test_db_t027_a_queued_message_is_answered_once_by_the_worker(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        model_agent="spark-test",
        mock_outbox_path=tmp_path / "mock-whatsapp.jsonl",
        contact_hash_pepper="worker-db-test-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
    )
    await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000000",
            city="Medellín",
        ),
        auth_subject=None,
    )
    jobs = PostgresJobQueue(db_sessions)
    ingest = Ingest(
        store=PostgresConversationStore(db_sessions),
        queue=PostgresInboundQueue(db_sessions, jobs),
        sellers=sellers_on_postgres(db_sessions),
        app_secret=SECRET,
    )
    body = webhook_fixture("text_question_shipping")

    first = await ingest.receive(body, sign(body, SECRET))
    again = await ingest.receive(body, sign(body, SECRET))

    assert first.queued == again.queued == ("wamid.VITRINA-FIXTURE-IN-0002",)
    providers = dataclasses.replace(
        compose(settings, InMemoryLedger(), mode=ProviderMode.replay),
        chat=ScriptedChat([final_answer(GREETING, confidence=0.95)]),
    )
    registry = TaskRegistry()
    load_tasks(registry)
    worker = Worker(jobs, registry, sessions=db_sessions, providers=providers, settings=settings)

    assert await worker.run_once() is True
    assert await worker.run_once() is False  # the redelivery added no job

    outbox = MockOutbox(settings.mock_outbox_path)
    assert [entry.text for entry in outbox.entries(contact="+573000000001")] == [GREETING]
    async with db_sessions() as session:
        rows = (
            await session.execute(
                sa.select(messages.c.direction, messages.c.delivery_status).order_by(
                    messages.c.created_at, messages.c.id
                )
            )
        ).all()
    assert [(row.direction, row.delivery_status) for row in rows] == [
        ("inbound", None),
        ("outbound", "mocked"),
    ]


async def test_db_fr_028_a_throttled_reply_comes_back_after_its_pause_without_a_new_turn(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        model_agent="spark-test",
        mock_outbox_path=tmp_path / "mock-whatsapp.jsonl",
        contact_hash_pepper="worker-db-test-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
    )
    await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000000",
            city="Medellín",
        ),
        auth_subject=None,
    )
    queue = PostgresJobQueue(db_sessions)
    ingest = Ingest(
        store=PostgresConversationStore(db_sessions),
        queue=PostgresInboundQueue(db_sessions, queue),
        sellers=sellers_on_postgres(db_sessions),
        app_secret=SECRET,
    )
    body = webhook_fixture("text_question_shipping")
    await ingest.receive(body, sign(body, SECRET))

    real = _messenger(settings)

    class ThrottledOnce(Messenger):
        throttled = False

        async def send(self, message: OutboundMessage) -> SendResult:
            if not self.throttled:
                self.throttled = True
                raise RateLimitedError("throttled", provider="whatsapp", retry_after_s=3600.0)
            return await real.send(message)

    monkeypatch.setitem(_MESSENGERS, id(settings), ThrottledOnce())
    chat = ScriptedChat([final_answer(GREETING, confidence=0.95)])
    providers = dataclasses.replace(
        compose(settings, InMemoryLedger(), mode=ProviderMode.replay), chat=chat
    )
    registry = TaskRegistry()
    load_tasks(registry)
    worker = Worker(queue, registry, sessions=db_sessions, providers=providers, settings=settings)

    assert await worker.run_once() is True
    assert await worker.run_once() is False  # it waits out the pause WhatsApp asked for

    async with db_sessions() as session, session.begin():
        await session.execute(sa.update(jobs_table).values(run_after=sa.func.now()))
    assert await worker.run_once() is True

    outbox = MockOutbox(settings.mock_outbox_path)
    assert [entry.text for entry in outbox.entries(contact="+573000000001")] == [GREETING]
    assert len(chat.requests) == 1
    async with db_sessions() as session:
        rows = (
            await session.execute(
                sa.select(messages.c.direction, messages.c.delivery_status).order_by(
                    messages.c.created_at, messages.c.id
                )
            )
        ).all()
    assert [(row.direction, row.delivery_status) for row in rows] == [
        ("inbound", None),
        ("outbound", "mocked"),
    ]
