"""The daily counter on Postgres: atomic across connections, so replicas share one count."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.quotas import PostgresUsageCounter

DAY = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.mark.db
async def test_db_concurrent_replicas_cannot_pass_the_limit(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    replicas = [PostgresUsageCounter(db_sessions) for _ in range(2)]

    taken = await asyncio.gather(
        *(replicas[index % 2].take("showcases", DAY, limit=3) for index in range(10))
    )

    assert sum(taken) == 3
    assert await PostgresUsageCounter(db_sessions).take("showcases", DAY, limit=3) is False
    assert await replicas[0].take("showcases", DAY + timedelta(days=1), limit=3) is True
    assert await replicas[0].take("another", DAY, limit=3) is True


@pytest.mark.db
async def test_db_several_at_once_are_whole_and_what_is_given_back_returns(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    counter = PostgresUsageCounter(db_sessions)

    assert await counter.take("jobs", DAY, limit=1, amount=2) is False
    assert await counter.take("jobs", DAY, limit=3, amount=2) is True
    assert await counter.take("jobs", DAY, limit=3, amount=2) is False
    await counter.give_back("jobs", DAY, amount=2)
    assert await counter.take("jobs", DAY, limit=3, amount=3) is True
    await counter.give_back("jobs", DAY, amount=9)  # never below zero
    await counter.give_back("nobody", DAY)
    assert await counter.take("jobs", DAY, limit=1) is True
    assert await counter.take("jobs", DAY, limit=1) is False
