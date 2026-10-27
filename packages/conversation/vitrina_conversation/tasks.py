"""Conversation jobs, discovered by the worker through the ``vitrina.tasks`` entry point."""

from uuid import UUID

from vitrina_conversation.pacing import Pacer
from vitrina_conversation.wiring import (
    PROCESS_INBOUND,
    messenger_for,
    postgres_pipeline,
    process_stored_message,
)
from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry
from vitrina_core.providers import Messenger
from vitrina_core.settings import Settings

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


def register(registry: TaskRegistry) -> None:
    registry.add(PROCESS_INBOUND, process_inbound_job, timeout_s=120)
