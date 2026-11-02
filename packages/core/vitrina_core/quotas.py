"""Daily limits on paid work, counted in Postgres so a restart or a second replica shares them.

A limit is a name and a UTC day (FR-085: timestamps are UTC). ``take`` is one atomic statement:
it either counts the whole amount or, when that would pass the limit, changes nothing, so
concurrent requests can never pass it together. The count lives in ``public_rate_limits``,
next to the public tool's hourly attempts. Without a database (tests, laptops) it lives in memory.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import public_rate_limits


def day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def seconds_to_next_day(now: datetime) -> int:
    """For ``Retry-After``: whole seconds, at least 1, until the UTC day ends."""
    left = day_start(now) + timedelta(days=1) - now
    return max(1, int(left.total_seconds()))


class UsageCounter(Protocol):
    async def take(self, key: str, window: datetime, *, limit: int, amount: int = 1) -> bool:
        """Count ``amount`` under ``key`` if the count stays within ``limit``; else change nothing."""
        ...

    async def give_back(self, key: str, window: datetime, *, amount: int = 1) -> None:
        """Undo a ``take`` whose work never started. The count never goes below zero."""
        ...


@dataclass
class InMemoryUsageCounter:
    """For tests and machines without a database; lost when the process ends."""

    counts: dict[tuple[str, datetime], int] = field(default_factory=lambda: defaultdict(int))

    async def take(self, key: str, window: datetime, *, limit: int, amount: int = 1) -> bool:
        if self.counts[key, window] + amount > limit:
            return False
        self.counts[key, window] += amount
        return True

    async def give_back(self, key: str, window: datetime, *, amount: int = 1) -> None:
        self.counts[key, window] = max(0, self.counts[key, window] - amount)


class PostgresUsageCounter:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def take(self, key: str, window: datetime, *, limit: int, amount: int = 1) -> bool:
        if amount > limit:  # the insert below would pass the limit on a key's first use
            return False
        table = public_rate_limits
        upsert = (
            postgres_insert(table)
            .values(ip_hash=key, window_start=window, count=amount)
            .on_conflict_do_update(
                index_elements=[table.c.ip_hash, table.c.window_start],
                set_={"count": table.c.count + amount},
                where=table.c.count + amount <= limit,
            )
            .returning(table.c.count)
        )
        async with self.sessions() as session, session.begin():
            return (await session.execute(upsert)).first() is not None

    async def give_back(self, key: str, window: datetime, *, amount: int = 1) -> None:
        table = public_rate_limits
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(table)
                .where(table.c.ip_hash == key, table.c.window_start == window)
                .values(count=sa.func.greatest(table.c.count - amount, 0))
            )
