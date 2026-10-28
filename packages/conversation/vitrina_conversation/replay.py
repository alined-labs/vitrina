"""Replay (T080; AS-8.1, FR-071): a recorded conversation through the normal pipeline.

Each fixture in ``evals/fixtures/replay`` holds the recorded webhook bodies of one synthetic
customer. A session starts a fresh replay conversation for that customer (closing the last
replay of the same fixture, never a live conversation), then feeds each message to
``process_inbound`` with ``is_replay`` through the messenger it is given, the local mock in
the worker, so nothing reaches WhatsApp. The gaps between the recorded timestamps are kept,
divided by ``speed`` (0.1 to 10). Message ids get the session id appended, so a fixture can
play again without being taken for a redelivery. The agent sees each message's recorded time,
so a fixture asks the model the same way every time and recorded outputs (cassettes, keyed by
the whole request) answer it again.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cache
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.api import ComplianceStore
from vitrina_conversation.pipeline import Inbound, InboundOutcome, PipelineDeps, process_inbound
from vitrina_conversation.store import ConversationStore, LiveConversationError
from vitrina_core.ids import new_id
from vitrina_core.jobs import PostgresJobQueue
from vitrina_core.settings import REPO_ROOT
from vitrina_whatsapp.webhook import InboundMessage, parse_webhook

REPLAY_FIXTURES = REPO_ROOT / "evals" / "fixtures" / "replay"
REPLAY_SESSION = "conversation.replay_session"
_FIXTURE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

__all__ = [
    "REPLAY_FIXTURES",
    "REPLAY_SESSION",
    "LiveConversationError",
    "ReplayFixture",
    "ReplaySession",
    "play",
    "replay_fixture",
    "replay_fixtures",
    "start_replay",
    "start_replay_session",
]


@dataclass(frozen=True)
class ReplayFixture:
    fixture_id: str
    name: str
    description: str
    messages: tuple[InboundMessage, ...]

    @property
    def message_count(self) -> int:
        return len(self.messages)


def replay_fixtures(folder: Path = REPLAY_FIXTURES) -> list[ReplayFixture]:
    """``GET /replay/fixtures``: every fixture, by id."""
    return [_load(path) for path in sorted(folder.glob("*.json"))]


def replay_fixture(fixture_id: str, folder: Path = REPLAY_FIXTURES) -> ReplayFixture | None:
    """One fixture by id; None for an unknown id or one that is not a plain name."""
    if not _FIXTURE_ID.fullmatch(fixture_id):
        return None
    path = folder / f"{fixture_id}.json"
    return _load(path) if path.is_file() else None


@cache
def _load(path: Path) -> ReplayFixture:
    document = json.loads(path.read_text())
    messages = tuple(
        message for body in document["webhooks"] for message in parse_webhook(body).messages
    )
    return ReplayFixture(path.stem, document["name"], document["description"], messages)


async def start_replay(
    store: ConversationStore,
    compliance: ComplianceStore,
    seller_id: UUID,
    fixture: ReplayFixture,
) -> UUID:
    """The fresh replay conversation the session plays into (``LiveConversationError`` if the
    fixture's customer has a live one with this seller)."""
    first = fixture.messages[0]
    contact = await compliance.resolve_contact(
        seller_id, first.from_wa_id, display_name=first.profile_name
    )
    conversation = await store.fresh_replay_conversation(seller_id, contact.id)
    return conversation.id


@dataclass(frozen=True)
class ReplaySession:
    """``POST /replay/sessions``' answer: the job plays the fixture into the conversation."""

    session_id: UUID
    conversation_id: UUID
    job_id: UUID


async def start_replay_session(
    *,
    sessions: async_sessionmaker[AsyncSession],
    store: ConversationStore,
    compliance: ComplianceStore,
    seller_id: UUID,
    fixture: ReplayFixture,
    speed: float = 1.0,
    use_recorded_model_outputs: bool = False,
) -> ReplaySession:
    """A fresh replay conversation and the ``replay`` job that plays the fixture into it."""
    conversation_id = await start_replay(store, compliance, seller_id, fixture)
    session_id = new_id()
    async with sessions() as session, session.begin():
        job_id = await PostgresJobQueue(sessions).enqueue(
            session,
            kind="replay",
            task=REPLAY_SESSION,
            payload={
                "fixture_id": fixture.fixture_id,
                "session_id": str(session_id),
                "conversation_id": str(conversation_id),
                "speed": speed,
                "use_recorded_model_outputs": use_recorded_model_outputs,
            },
            owner_seller_id=seller_id,
        )
    return ReplaySession(session_id, conversation_id, job_id)


async def play(
    fixture: ReplayFixture,
    deps: PipelineDeps,
    seller_id: UUID,
    session_id: UUID,
    *,
    speed: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[InboundOutcome]:
    """Each recorded message through the pipeline, after its recorded gap divided by ``speed``."""
    outcomes: list[InboundOutcome] = []
    previous = None
    for message in fixture.messages:
        if previous is not None:
            await sleep(max(0.0, (message.sent_at - previous).total_seconds()) / speed)
        previous = message.sent_at
        inbound = Inbound(
            seller_id=seller_id,
            wa_id=message.from_wa_id,
            message_id=f"{message.message_id}.{session_id}",
            kind=message.kind,
            text=message.text,
            profile_name=message.profile_name,
            wa_type=message.wa_type,
            is_replay=True,
            media_id=message.media.media_id if message.media else None,
        )
        agent = replace(deps.agent, clock=_frozen(message.sent_at))
        outcomes.append(await process_inbound(inbound, replace(deps, agent=agent)))
    return outcomes


def _frozen(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment
