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
