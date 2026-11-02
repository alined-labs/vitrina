"""Async engine and sessions on the hosted Supabase Postgres (plan.md §1, §3)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vitrina_core.settings import Settings


def search_path(schema: str) -> str:
    """The machine schema first; pgvector lives in public (CI) or extensions (Supabase)."""
    return f'"{schema}", public, extensions'


def create_engine(settings: Settings) -> AsyncEngine:
    if settings.db_schema is None:
        raise LookupError("DB_SCHEMA is not set: the schema to use (.env.example)")
    engine = create_async_engine(
        settings.async_database_url(),
        # Three machines and CI share one Supabase project: keep pools small.
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    path = search_path(settings.db_schema)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_search_path(dbapi_connection: Any, _record: Any) -> None:
        # A SET per connection also works behind the Supabase session pooler. The DBAPI opens
        # a transaction for it, so commit: a SET in a transaction that rolls back is undone,
        # and the next session on this pooled connection would read the wrong schema.
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET search_path TO {path}")
        cursor.close()
        dbapi_connection.commit()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on any error."""
    async with factory() as session, session.begin():
        yield session


@asynccontextmanager
async def customer_work_lock(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID
) -> AsyncIterator[None]:
    """One at a time per seller: answering a customer message, or erasing a contact.

    They run in different worker processes, and an answer still being written when the erasure
    ran would keep the customer's words after the receipt says they are gone. The lock lives in
    its own transaction, which only ever rolls back, so a cancelled job or a dead connection
    releases it. It takes the id's last 8 bytes: bookings lock on the first 8, and a turn books
    while it holds this one.
    """
    key = int.from_bytes(seller_id.bytes[8:], "big", signed=True)
    async with sessions() as session:
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.cast(key, sa.BigInteger))))
        yield
