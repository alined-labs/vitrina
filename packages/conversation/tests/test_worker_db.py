"""T027 end to end on Postgres: the webhook ingest queues, the worker answers (CI's db job).

The real job queue, worker and Postgres stores; the scripted Spark and the local mock
messenger stand in for the providers.
"""

import asyncio
import dataclasses
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.erasure import request_erasure
from vitrina_compliance.tables import contacts
from vitrina_conversation.api import (
    PROCESS_INBOUND,
    Ingest,
    PostgresConversationStore,
    PostgresInboundQueue,
    sellers_on_postgres,
)
from vitrina_conversation.booking import PostgresBookings
from vitrina_conversation.tables import messages
from vitrina_conversation.testing import ScriptedChat, final_answer, webhook_fixture
from vitrina_core.db import customer_work_lock
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import ChatRequest, ChatResponse
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


async def test_db_an_erasure_requested_mid_turn_also_erases_the_reply_written_after_it(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The two workers run at once, but an erasure still covers the answer being written."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        model_agent="spark-test",
        mock_outbox_path=tmp_path / "mock-whatsapp.jsonl",
        contact_hash_pepper="worker-db-test-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
    )
    seller = await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000000",
            city="Medellín",
        ),
        auth_subject=None,
    )
    jobs = PostgresJobQueue(db_sessions)
    await Ingest(
        store=PostgresConversationStore(db_sessions),
        queue=PostgresInboundQueue(db_sessions, jobs),
        sellers=sellers_on_postgres(db_sessions),
        app_secret=SECRET,
    ).receive(
        body := webhook_fixture("text_question_shipping"),
        sign(body, SECRET),
    )
    registry = TaskRegistry()
    load_tasks(registry)
    general = Worker(
        jobs,
        registry.narrowed(exclude={PROCESS_INBOUND}),
        sessions=db_sessions,
        settings=settings,
    )
    erasing: list[asyncio.Task[bool]] = []

    class ErasureRequestedMidTurn(ScriptedChat):
        async def complete(self, request: ChatRequest) -> ChatResponse:
            if request.purpose != "classify" and not erasing:
                async with db_sessions() as session, session.begin():
                    contact_id = await session.scalar(sa.select(contacts.c.id))
                    await request_erasure(jobs, session, seller.id, contact_id)
                erasing.append(asyncio.create_task(general.run_once()))
                await asyncio.sleep(1.0)  # far longer than an idle worker takes to erase
            return await super().complete(request)

    inbound = Worker(
        jobs,
        registry.narrowed(only={PROCESS_INBOUND}),
        sessions=db_sessions,
        providers=dataclasses.replace(
            compose(settings, InMemoryLedger(), mode=ProviderMode.replay),
            chat=ErasureRequestedMidTurn([final_answer(GREETING, confidence=0.95)]),
        ),
        settings=settings,
    )

    assert await inbound.run_once() is True
    assert await erasing[0] is True

    async with db_sessions() as session:
        rows = (
            await session.execute(
                sa.select(messages.c.direction, messages.c.text, messages.c.agent_output)
            )
        ).all()
        erased_at = await session.scalar(sa.select(contacts.c.erased_at))
    assert erased_at is not None
    assert sorted(row.direction for row in rows) == ["inbound", "outbound"]
    assert [(row.text, row.agent_output) for row in rows] == [(None, None), (None, None)]
    outbox = MockOutbox(settings.mock_outbox_path)
    assert [entry.text for entry in outbox.entries(contact="+573000000001")] == [GREETING]


async def test_db_a_booking_during_a_turn_does_not_wait_for_the_turns_own_lock(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = uuid4()

    async with customer_work_lock(db_sessions, seller_id):
        async with asyncio.timeout(5):
            async with PostgresBookings(db_sessions).locked(seller_id):
                pass


async def test_db_the_lock_keeps_one_holder_per_seller_and_lets_other_sellers_through(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = uuid4()

    async def second_holder() -> None:
        async with customer_work_lock(db_sessions, seller_id):
            pass

    async with customer_work_lock(db_sessions, seller_id):
        waiting = asyncio.create_task(second_holder())
        await asyncio.sleep(0.5)
        assert not waiting.done()
        async with asyncio.timeout(5), customer_work_lock(db_sessions, uuid4()):
            pass
    async with asyncio.timeout(5):
        await waiting


async def test_db_a_cancelled_holder_releases_the_lock(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = uuid4()

    async def holder() -> None:
        async with customer_work_lock(db_sessions, seller_id):
            await asyncio.sleep(60)

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    async with asyncio.timeout(5), customer_work_lock(db_sessions, seller_id):
        pass
