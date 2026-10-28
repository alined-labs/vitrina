"""Reservation bookkeeping (T052, AS-3.7): the worker marks expired reservations.

Stock never waits for this sweep: ``products.reserved`` already ignores reservations past
``expires_at`` (data-model.md). The sweep only makes ``status`` say so.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.tables import reservations


async def expire_reservations(sessions: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Every seller's reservations past ``expires_at`` become ``expired``; returns how many."""
    async with sessions() as session, session.begin():
        expired = await session.execute(
            sa.update(reservations)
            .where(reservations.c.status == "reserved", reservations.c.expires_at <= now)
            .values(status="expired")
            .returning(reservations.c.id)
        )
        return len(expired.all())
