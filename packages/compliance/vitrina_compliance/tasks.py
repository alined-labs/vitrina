"""Compliance jobs, discovered by the worker through the ``vitrina.tasks`` entry point."""

from datetime import UTC, datetime
from uuid import UUID

from vitrina_compliance.consents import PostgresCompliance
from vitrina_compliance.erasure import ERASURE_TASK, load_erasers
from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry


async def erase_contact(ctx: JobContext) -> str:
    """AS-6.6: every registered part first, then the contact itself, then the receipt."""
    seller_id = ctx.owner_seller_id
    if ctx.sessions is None or seller_id is None:
        raise JobFailedError("INTERNAL_ERROR", "erasure needs the database and the seller")
    try:
        contact_id = UUID(str(ctx.payload.get("contact_id")))
    except ValueError as error:
        raise JobFailedError("VALIDATION_ERROR", "the job names no contact") from error
    requested = ctx.payload.get("requested_at")
    requested_at = datetime.fromisoformat(requested) if requested else datetime.now(UTC)
    parts = ["contact"]
    for eraser in load_erasers():
        parts.append(await eraser(ctx, seller_id, contact_id))
    await ctx.progress("erasing", 0.8)
    try:
        await PostgresCompliance(ctx.sessions, None).erase(
            seller_id, contact_id, requested_at=requested_at, scope=tuple(parts)
        )
    except LookupError as error:
        raise JobFailedError("NOT_FOUND", str(error)) from error
    return f"/v1/contacts/{contact_id}"


def register(registry: TaskRegistry) -> None:
    registry.add(ERASURE_TASK, erase_contact, timeout_s=600)
