"""Cross-module events (plan.md §3; data-model.md §4).

A module never reads another's tables: it emits an event in the same transaction as its
own write, and consumers read the ``events`` table (a NOTIFY wakes them). Payloads follow
data-model.md §4.
"""

from collections.abc import Mapping
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
