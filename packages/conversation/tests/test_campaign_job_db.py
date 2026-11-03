"""FR-064 on Postgres (CI's db job): what the send job does with the way a pass ended."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import InMemoryCatalog
from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import CAMPAIGN_SEND, SendRun
from vitrina_conversation import campaigns
from vitrina_conversation.api import PostgresConversationStore
from vitrina_conversation.pacing import Pacer
from vitrina_core.db.tables import jobs
from vitrina_core.jobs import JobContext, JobFailedError, PostgresJobQueue
from vitrina_core.jobs.queue import ClaimedJob
from vitrina_core.providers import OutboundMessage, SendResult
from vitrina_core.settings import Settings

pytestmark = pytest.mark.db

CAMPAIGN = UUID("00000000-0000-7000-8000-0000000000c1")
RESUME = datetime(2026, 10, 28, 15, 1, tzinfo=UTC)


class NeverSends:
    async def send(self, message: OutboundMessage) -> SendResult:
        raise AssertionError("the pass is scripted: nothing is sent")


async def run_job(
    db_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    outcome: SendRun,
    pauses: int,
) -> None:
    """The job with a scripted pass, as the worker would run it after ``pauses`` pauses."""

    async def scripted_pass(*_args: Any, **given: Any) -> SendRun:
        assert given["pauses"] == pauses
        return outcome

    monkeypatch.setattr(campaigns, "run_send", scripted_pass)
    seller = await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000241",
            city="Medellín",
        ),
        auth_subject=None,
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        mock_outbox_path=tmp_path / "outbox.jsonl",
        contact_hash_pepper="campaign-job-db-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
    )
    job = ClaimedJob(
        id=CAMPAIGN,
        kind="campaign_send",
        task=CAMPAIGN_SEND,
        payload={"campaign_id": str(CAMPAIGN), "pauses": pauses},
        owner_seller_id=seller.id,
        attempt=1,
        max_attempts=3,
    )
    await campaigns.campaign_send_job(
        JobContext(job, PostgresJobQueue(db_sessions), db_sessions, settings=settings),
        messenger=NeverSends(),
        pacer=Pacer(),
        store=PostgresConversationStore(db_sessions),
        catalog=InMemoryCatalog(),
    )


async def queued_payloads(db_sessions: async_sessionmaker[AsyncSession]) -> list[Any]:
    async with db_sessions() as session:
        rows = await session.execute(sa.select(jobs.c.payload, jobs.c.run_after))
    return [(row.payload.get("pauses"), row.run_after) for row in rows]


async def test_db_fr_064_a_paused_pass_queues_the_next_one_and_counts_the_pause(
    db_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outcome = SendRun(None, RESUME, paused=True)  # type: ignore[arg-type]

    await run_job(db_sessions, monkeypatch, tmp_path, outcome=outcome, pauses=2)

    assert await queued_payloads(db_sessions) == [(3, RESUME)]


async def test_db_fr_064_a_pass_that_got_through_starts_the_pause_count_again(
    db_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    later = RESUME + timedelta(days=1)
    outcome = SendRun(None, later)  # type: ignore[arg-type]

    await run_job(db_sessions, monkeypatch, tmp_path, outcome=outcome, pauses=2)

    assert await queued_payloads(db_sessions) == [(0, later)]


async def test_db_fr_064_a_campaign_that_ended_early_fails_its_job_and_queues_nothing(
    db_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    why = "Cloud API refused the request (HTTP 401, code 190)"
    outcome = SendRun(None, None, stopped=why)  # type: ignore[arg-type]

    with pytest.raises(JobFailedError) as failed:
        await run_job(db_sessions, monkeypatch, tmp_path, outcome=outcome, pauses=0)

    assert (failed.value.code, failed.value.message) == ("PROVIDER_ERROR", why)
    assert await queued_payloads(db_sessions) == []
