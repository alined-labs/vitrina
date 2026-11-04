"""T027 end to end on Postgres: the webhook ingest queues, the worker answers (CI's db job).

The real job queue, worker and Postgres stores; the scripted Spark and the local mock
messenger stand in for the providers.
"""

import asyncio
import dataclasses
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.erasure import ERASURE_TASK, request_erasure
from vitrina_compliance.tables import contacts
from vitrina_conversation.api import (
    PROCESS_INBOUND,
    Ingest,
    PostgresConversationStore,
    PostgresInboundQueue,
    sellers_on_postgres,
)
from vitrina_conversation.tables import messages
from vitrina_conversation.tasks import _MESSENGERS, _messenger
from vitrina_conversation.testing import ScriptedChat, final_answer, webhook_fixture
from vitrina_core.db.tables import jobs as job_rows
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import (
    ChatRequest,
    ChatResponse,
    Messenger,
    OutboundMessage,
    RateLimitedError,
    SendResult,
)
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import REPO_ROOT, Settings
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


def deployed_inbound_lane() -> frozenset[str]:
    """The tasks production gives the inbound worker, read from the script that deploys it."""
    found = re.search(
        r"^INBOUND_WORKER_TASKS=(.+)$", (REPO_ROOT / "deploy" / "azure.sh").read_text(), re.M
    )
    assert found is not None
    return frozenset(found.group(1).split(","))


async def test_db_an_erasure_requested_mid_turn_waits_in_the_lane_and_leaves_no_words_behind(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The deployed lanes on Postgres: the other worker's claim cannot take the erasure, and the
    lane's own loop runs it after the turn. One replica per lane is what makes that loop serial;
    ``claim`` alone would hand the erasure to a second replica of the lane."""
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
    body = webhook_fixture("text_question_shipping")
    await Ingest(
        store=PostgresConversationStore(db_sessions),
        queue=PostgresInboundQueue(db_sessions, jobs),
        sellers=sellers_on_postgres(db_sessions),
        app_secret=SECRET,
    ).receive(body, sign(body, SECRET))
    registry = TaskRegistry()
    load_tasks(registry)
    lane_tasks = deployed_inbound_lane()
    general = Worker(
        jobs, registry.narrowed(exclude=lane_tasks), sessions=db_sessions, settings=settings
    )
    general_claimed: list[bool] = []
    erasure_during_the_turn: list[str | None] = []

    class ErasureRequestedMidTurn(ScriptedChat):
        async def complete(self, request: ChatRequest) -> ChatResponse:
            if request.purpose != "classify" and not general_claimed:
                async with db_sessions() as session, session.begin():
                    contact_id = await session.scalar(sa.select(contacts.c.id))
                    await request_erasure(jobs, session, seller.id, contact_id)
                general_claimed.append(await general.run_once())
                await asyncio.sleep(1.0)  # far longer than an idle worker takes to erase
                async with db_sessions() as session:
                    erasure_during_the_turn.append(
                        await session.scalar(
                            sa.select(job_rows.c.status).where(job_rows.c.task == ERASURE_TASK)
                        )
                    )
            return await super().complete(request)

    lane = Worker(
        jobs,
        registry.narrowed(only=lane_tasks),
        sessions=db_sessions,
        providers=dataclasses.replace(
            compose(settings, InMemoryLedger(), mode=ProviderMode.replay),
            chat=ErasureRequestedMidTurn([final_answer(GREETING, confidence=0.95)]),
        ),
        settings=settings,
        poll_interval_s=0.05,
    )

    async def finished_jobs() -> dict[str, sa.Row[tuple[str, object, object]]]:
        async with db_sessions() as session:
            rows = await session.execute(
                sa.select(
                    job_rows.c.task,
                    job_rows.c.status,
                    job_rows.c.started_at,
                    job_rows.c.finished_at,
                ).where(job_rows.c.finished_at.is_not(None))
            )
        return {row.task: row for row in rows}

    stop = asyncio.Event()
    running = asyncio.create_task(lane.run(stop))
    try:
        async with asyncio.timeout(60):
            done = await finished_jobs()
            for _ in range(600):
                if len(done) == 2:
                    break
                await asyncio.sleep(0.1)
                done = await finished_jobs()
    finally:
        stop.set()
        await running

    assert general_claimed == [False]
    assert erasure_during_the_turn == ["queued"]
    assert {task: row.status for task, row in done.items()} == {
        PROCESS_INBOUND: "succeeded",
        ERASURE_TASK: "succeeded",
    }
    assert done[ERASURE_TASK].started_at >= done[PROCESS_INBOUND].finished_at
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
        await session.execute(sa.update(job_rows).values(run_after=sa.func.now()))
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
