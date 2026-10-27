"""Conversation jobs, discovered by the worker through the ``vitrina.tasks`` entry point."""

import dataclasses
from uuid import UUID

from vitrina_conversation.pacing import Pacer
from vitrina_conversation.replay import REPLAY_SESSION, play, replay_fixture
from vitrina_conversation.wiring import (
    PROCESS_INBOUND,
    messenger_for,
    postgres_pipeline,
    process_stored_message,
)
from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry
from vitrina_core.ledger import PostgresLedger, SpendWatch
from vitrina_core.providers import Messenger
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_local_mock import LocalMockMessenger, MockOutbox

# One of each per worker process: the pacer keeps the 6 s spacing per recipient (R12), and
# the messenger keeps its HTTP connection pool.
_PACER = Pacer()
_MESSENGERS: dict[int, Messenger] = {}


def _messenger(settings: Settings) -> Messenger:
    messenger = _MESSENGERS.get(id(settings))
    if messenger is None:
        messenger = _MESSENGERS[id(settings)] = messenger_for(settings)
    return messenger


async def process_inbound_job(ctx: JobContext) -> str | None:
    """T027: answer one queued WhatsApp message from its kept webhook body."""
    seller_id = ctx.owner_seller_id
    if ctx.sessions is None or ctx.providers is None or seller_id is None:
        raise JobFailedError("INTERNAL_ERROR", "needs the database, the providers and the seller")
    try:
        deps = postgres_pipeline(
            settings=ctx.settings,
            sessions=ctx.sessions,
            providers=ctx.providers,
            messenger=_messenger(ctx.settings),
            pacer=_PACER,
            storage=ctx.storage,
        )
    except LookupError as error:  # MODEL_AGENT unset (Q01)
        raise JobFailedError("PROVIDER_UNAVAILABLE", str(error)) from error
    outcome = await process_stored_message(
        deps, seller_id, UUID(str(ctx.payload["payload_id"])), str(ctx.payload["message_id"])
    )
    if outcome is None:
        raise JobFailedError("NOT_FOUND", "the webhook payload or its message is no longer kept")
    return f"/v1/conversations/{outcome.conversation_id}" if outcome.conversation_id else None


async def replay_session_job(ctx: JobContext) -> str | None:
    """T080: play a fixture into its replay conversation, through the local mock (FR-071)."""
    seller_id, payload = ctx.owner_seller_id, ctx.payload
    if ctx.sessions is None or ctx.providers is None or seller_id is None:
        raise JobFailedError("INTERNAL_ERROR", "needs the database, the providers and the seller")
    fixture = replay_fixture(str(payload["fixture_id"]))
    if fixture is None:
        raise JobFailedError("NOT_FOUND", "the replay fixture is gone")
    speed = float(payload.get("speed", 1.0))
    providers = ctx.providers
    if payload.get("use_recorded_model_outputs") and providers.mode is not ProviderMode.replay:
        ledger = PostgresLedger(ctx.sessions, SpendWatch(ctx.settings.spend_limit_usd))
        offline = compose(ctx.settings, ledger, mode=ProviderMode.replay)
        providers = dataclasses.replace(providers, chat=offline.chat)
    try:
        deps = postgres_pipeline(
            settings=ctx.settings,
            sessions=ctx.sessions,
            providers=providers,
            messenger=LocalMockMessenger(MockOutbox(ctx.settings.mock_outbox_path)),
            pacer=Pacer(ctx.settings.outbound_spacing_s / speed),
            storage=ctx.storage,
        )
    except LookupError as error:  # MODEL_AGENT unset (Q01)
        raise JobFailedError("PROVIDER_UNAVAILABLE", str(error)) from error
    await play(fixture, deps, seller_id, UUID(str(payload["session_id"])), speed=speed)
    return f"/v1/conversations/{payload['conversation_id']}"


def register(registry: TaskRegistry) -> None:
    registry.add(PROCESS_INBOUND, process_inbound_job, timeout_s=120)
    # The slowest fixture at speed 0.1: its recorded gaps, the 6 s spacing and three turns.
    registry.add(REPLAY_SESSION, replay_session_job, timeout_s=900)
