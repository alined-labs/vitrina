"""The conversation module's live counters for ``GET /metrics/summary`` (T073, FR-070).

Registered under the ``vitrina.metrics`` entry point, so evals reads no conversation table
itself (Art. VI.3). Replays never count. ``conversations_today``: chats with a customer
message since ``since``, the start of the seller's day; ``attributed_rate``: the share of them
an ad started; ``handoffs_open``: handoffs the owner has not released, whenever they opened.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_conversation.tables import conversations, handoffs, messages


async def counters(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID, since: datetime, /
) -> Mapping[str, int | float]:
    today = (
        sa.select(conversations.c.attribution_status)
        .where(
            conversations.c.seller_id == seller_id,
            conversations.c.is_replay.is_(False),
            sa.exists().where(
                messages.c.conversation_id == conversations.c.id,
                messages.c.direction == "inbound",
                messages.c.created_at >= since,
            ),
        )
        .subquery()
    )
    chats = sa.select(
        sa.func.count(), sa.func.count().filter(today.c.attribution_status == "attributed")
    ).select_from(today)
    waiting = (
        sa.select(sa.func.count())
        .select_from(handoffs.join(conversations, handoffs.c.conversation_id == conversations.c.id))
        .where(
            handoffs.c.seller_id == seller_id,
            handoffs.c.released_at.is_(None),
            conversations.c.is_replay.is_(False),
        )
    )
    async with sessions() as session:
        total, attributed = (await session.execute(chats)).one()
        open_handoffs = (await session.execute(waiting)).scalar_one()
    return {
        "conversations_today": total,
        "attributed_rate": round(attributed / total, 4) if total else 0.0,
        "handoffs_open": open_handoffs,
    }
