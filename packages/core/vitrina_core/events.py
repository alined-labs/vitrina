"""Cross-module events (plan.md §3; data-model.md §4).

A module never reads another's tables: it emits an event in the same transaction as its
own write, and consumers read the ``events`` table (a NOTIFY wakes them). Payloads follow
data-model.md §4.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from vitrina_core.db.tables import events

EventType = Literal[
    "ad.chat_started",
    "ad.reservation",
    "ad.booking",
    "ad.handoff",
    "ad.objection",
    "objection.recorded",
    "consent.recorded",
    "consent.revoked",
    "creative.verified",
    "campaign.decision",
]


async def emit(
    session: AsyncSession, event: EventType, payload: Mapping[str, Any], *, seller_id: UUID | None
) -> None:
    """Insert inside the caller's transaction: the event commits with the change it reports."""
    await session.execute(
        sa.insert(events).values(seller_id=seller_id, type=event, payload=dict(payload))
    )


@dataclass(frozen=True)
class StoredEvent:
    id: int
    seller_id: UUID | None
    type: str
    payload: dict[str, Any]
    created_at: datetime


async def events_after(
    session: AsyncSession, after_id: int, *, types: Collection[EventType], limit: int
) -> list[StoredEvent]:
    """The next events past ``after_id``, in id order: consumers keep the cursor and are
    idempotent on ``id`` (data-model.md)."""
    rows = await session.execute(
        sa.select(events)
        .where(events.c.id > after_id, events.c.type.in_(sorted(types)))
        .order_by(events.c.id)
        .limit(limit)
    )
    return [
        StoredEvent(row.id, row.seller_id, row.type, dict(row.payload), row.created_at)
        for row in rows
    ]


async def tally(
    session: AsyncSession,
    seller_id: UUID,
    event: EventType,
    *,
    start: datetime,
    end: datetime,
    by: Sequence[str],
) -> dict[tuple[str | None, ...], int]:
    """How many ``event`` rows the seller has in ``[start, end)``, by payload keys."""
    keys = [events.c.payload[key].astext for key in by]
    rows = await session.execute(
        sa.select(*keys, sa.func.count())
        .where(
            events.c.seller_id == seller_id,
            events.c.type == event,
            events.c.created_at >= start,
            events.c.created_at < end,
        )
        .group_by(*keys)
    )
    return {tuple(row[:-1]): int(row[-1]) for row in rows}
