import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.http import PostgresIdempotencyStore
from vitrina_core.ids import NIL

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
KEY = "key-0001-abcd"


@pytest.mark.db
async def test_db_fr_081_store_claims_replays_and_detects_conflicts(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresIdempotencyStore(db_sessions)

    assert (await store.begin(SELLER, KEY, "hash-1")).kind == "new"
    assert (await store.begin(SELLER, KEY, "hash-1")).kind == "in_progress"
    await store.finish(SELLER, KEY, 201, {"id": "p-1"})
    replay = await store.begin(SELLER, KEY, "hash-1")
    assert (replay.kind, replay.status, replay.body) == ("replay", 201, {"id": "p-1"})
    assert (await store.begin(SELLER, KEY, "hash-2")).kind == "conflict"
    assert (await store.begin(NIL, KEY, "hash-2")).kind == "new"
    await store.abandon(SELLER, KEY)
    assert (await store.begin(SELLER, KEY, "hash-2")).kind == "new"


@pytest.mark.db
async def test_db_fr_081_keys_older_than_24_hours_are_reclaimed(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresIdempotencyStore(db_sessions)
    await store.begin(SELLER, KEY, "hash-1")
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.text("UPDATE idempotency_keys SET created_at = now() - interval '25 hours'")
        )

    assert (await store.begin(SELLER, KEY, "hash-2")).kind == "new"


@pytest.mark.db
async def test_db_fr_081_of_many_retries_of_a_stale_row_exactly_one_runs(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresIdempotencyStore(db_sessions, stale_after=timedelta(seconds=90))
    await store.begin(SELLER, KEY, "hash-1")
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.text("UPDATE idempotency_keys SET created_at = now() - interval '91 seconds'")
        )

    outcomes = await asyncio.gather(*(store.begin(SELLER, KEY, "hash-1") for _ in range(5)))

    assert sorted(outcome.kind for outcome in outcomes) == ["in_progress"] * 4 + ["new"]


@pytest.mark.db
async def test_db_fr_081_a_row_that_is_renewed_does_not_go_stale(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresIdempotencyStore(db_sessions, stale_after=timedelta(seconds=90))
    await store.begin(SELLER, KEY, "hash-1")
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.text("UPDATE idempotency_keys SET created_at = now() - interval '91 seconds'")
        )

    await store.renew(SELLER, KEY)

    assert (await store.begin(SELLER, KEY, "hash-1")).kind == "in_progress"


@pytest.mark.db
async def test_db_fr_081_a_stale_row_keeps_its_request_and_a_finished_row_is_not_reclaimed(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresIdempotencyStore(db_sessions, stale_after=timedelta(seconds=90))
    await store.begin(SELLER, KEY, "hash-1")
    await store.begin(SELLER, "another-key", "hash-1")
    await store.finish(SELLER, "another-key", 201, {"id": "p-1"})
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.text("UPDATE idempotency_keys SET created_at = now() - interval '91 seconds'")
        )
    await store.renew(SELLER, "another-key")  # a finished row is left as it is

    assert (await store.begin(SELLER, KEY, "hash-2")).kind == "conflict"
    assert (await store.begin(SELLER, "another-key", "hash-1")).kind == "replay"
    async with db_sessions() as session:
        age = await session.scalar(
            sa.text(
                "SELECT now() - created_at > interval '90 seconds' FROM idempotency_keys"
                " WHERE key = 'another-key'"
            )
        )
    assert age is True
