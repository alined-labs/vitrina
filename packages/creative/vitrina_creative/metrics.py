"""The creative module's live counters for ``GET /metrics/summary`` (T073, FR-070).

Registered under the ``vitrina.metrics`` entry point; evals reads no creative table itself
(Art. VI.3). ``creatives_blocked_total`` counts every scene the fidelity verifier blocked for
the seller, since the first job: how often the verifier kept an altered product off an ad.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_creative.tables import creatives


async def counters(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID, since: datetime, /
) -> Mapping[str, int | float]:
    """``since`` (start of today) is unused: the counter is a running total."""
    query = sa.select(sa.func.count()).where(
        creatives.c.seller_id == seller_id,
        creatives.c.verification["status"].astext == "blocked",
    )
    async with sessions() as session:
        blocked = (await session.execute(query)).scalar_one()
    return {"creatives_blocked_total": blocked}
