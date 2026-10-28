"""Live counters for ``GET /metrics/summary`` (T073, FR-070): each module counts its own rows.

A module registers ``async def count(sessions, seller_id, since) -> Mapping[str, int | float]``
under the ``vitrina.metrics`` entry-point group, the way erasure parts register under
``vitrina.erasure``: evals never reads another module's tables (Art. VI.3). ``since`` is the
start of today in Bogotá. Keys outside ``LiveCounters`` are dropped, a module that fails is
logged and left out, and a counter nobody reports stays absent from the answer.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time
from importlib.metadata import EntryPoint, entry_points
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_evals.schemas import LiveCounters

METRICS_ENTRY_POINT = "vitrina.metrics"
BOGOTA = ZoneInfo("America/Bogota")

logger = logging.getLogger("vitrina.evals")


class Counters(Protocol):
    async def __call__(
        self, sessions: async_sessionmaker[AsyncSession], seller_id: UUID, since: datetime, /
    ) -> Mapping[str, int | float]: ...


class _Loadable(Protocol):
    name: str

    def load(self) -> Counters: ...


def load_counters(points: Iterable[EntryPoint | _Loadable] | None = None) -> list[Counters]:
    found = points if points is not None else entry_points(group=METRICS_ENTRY_POINT)
    counters: list[Counters] = []
    for point in found:
        try:
            counters.append(point.load())
        except Exception:
            logger.exception("live counters from %s are off: they failed to load", point.name)
    return counters


async def live_counters(
    counters: Sequence[Counters],
    sessions: async_sessionmaker[AsyncSession] | None,
    seller_id: UUID,
    *,
    now: datetime,
) -> LiveCounters:
    if sessions is None:
        return LiveCounters()
    since = datetime.combine(now.astimezone(BOGOTA).date(), time.min, tzinfo=BOGOTA)
    values: dict[str, int | float] = {}
    for count in counters:
        try:
            reported = await count(sessions, seller_id, since)
        except Exception:
            logger.exception("a live counter failed; its values are left out")
            continue
        values.update(
            {key: value for key, value in reported.items() if key in LiveCounters.model_fields}
        )
    return LiveCounters.model_validate(values)
