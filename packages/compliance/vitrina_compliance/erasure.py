"""Erasing a contact on request (FR-065, AS-6.6; Art. II.3: within 24 hours).

``DELETE /v1/contacts/{id}`` and the ``BORRAR MIS DATOS`` keyword both enqueue one
``erasure`` job. The job asks each module registered under the ``vitrina.erasure`` entry
point to erase its part (the conversation module: message content), then erases what
compliance holds: the number and the name; the contact stays opted out. The receipt names
every part, so it shows what was erased:

    [project.entry-points."vitrina.erasure"]
    conversation = "vitrina_conversation.erasure:erase"   # async (ctx, seller_id, contact_id) -> "messages"

A failing part fails the job, and the worker retries it well inside the 24 hours.
"""

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from importlib.metadata import EntryPoint, entry_points
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from vitrina_core.jobs import JobContext, PostgresJobQueue

ERASURE_TASK = "compliance.erase_contact"
ERASERS_ENTRY_POINT = "vitrina.erasure"

Eraser = Callable[[JobContext, UUID, UUID], Awaitable[str]]


class _Loadable(Protocol):
    def load(self) -> Eraser: ...


def load_erasers(points: Iterable[EntryPoint | _Loadable] | None = None) -> list[Eraser]:
    found = points if points is not None else entry_points(group=ERASERS_ENTRY_POINT)
    return [point.load() for point in found]


async def request_erasure(
    jobs: PostgresJobQueue, session: AsyncSession, seller_id: UUID, contact_id: UUID
) -> UUID:
    """Enqueue the erasure in the caller's transaction; returns the job id to poll."""
    return await jobs.enqueue(
        session,
        kind="erasure",
        task=ERASURE_TASK,
        payload={
            "contact_id": str(contact_id),
            "requested_at": datetime.now(UTC).isoformat(),
        },
        owner_seller_id=seller_id,
    )
