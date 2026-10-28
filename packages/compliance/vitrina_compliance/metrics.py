"""The compliance module's live counter for ``GET /metrics/summary`` (T073, FR-070).

Registered under the ``vitrina.metrics`` entry point (Art. VI.3). ``blocked_sends_today``: the
campaign sends eligibility refused since ``since``, the start of the seller's day, for lack of
consent, an opt-out, the frequency cap or a matching ad (FR-061); a contact evaluated twice
in one campaign counts once.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.tables import send_decisions


async def counters(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID, since: datetime, /
) -> Mapping[str, int | float]:
    blocked = (
        sa.select(send_decisions.c.campaign_id, send_decisions.c.contact_id)
        .where(
            send_decisions.c.seller_id == seller_id,
            send_decisions.c.decision == "blocked",
            send_decisions.c.evaluated_at >= since,
        )
        .distinct()
        .subquery()
    )
    async with sessions() as session:
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(blocked))
        ).scalar_one()
    return {"blocked_sends_today": count}
