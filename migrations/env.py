"""Alembic environment: one schema per machine on the shared Supabase project (plan.md §3).

Programmatic callers (tests) may override the target with ``config.attributes["schema"]``
and ``config.attributes["url"]``; otherwise DB_SCHEMA and DATABASE_URL apply.
"""

import asyncio

import sqlalchemy as sa
from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from vitrina_core.db import search_path
from vitrina_core.settings import Settings, get_settings

config = context.config


def target() -> tuple[str, str | None]:
    settings = get_settings()
    schema = config.attributes.get("schema") or settings.db_schema
    if not schema:
        raise SystemExit("DB_SCHEMA is not set: the schema to migrate (.env.example)")
    url = config.attributes.get("url")
    if url:
        url = Settings(_env_file=None, database_url=url).async_database_url()  # type: ignore[call-arg]
    elif settings.database_url is not None:
        url = settings.async_database_url()
    return schema, url


def prepare(schema: str) -> list[str]:
    return [f'CREATE SCHEMA IF NOT EXISTS "{schema}"', f"SET search_path TO {search_path(schema)}"]


def run_migrations_offline() -> None:
    schema, _ = target()
    context.configure(
        url="postgresql+asyncpg://offline",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=schema,
    )
    with context.begin_transaction():
        for statement in prepare(schema):
            context.execute(statement)
        context.run_migrations()


def run_sync_migrations(connection: Connection, schema: str) -> None:
    for statement in prepare(schema):
        connection.execute(sa.text(statement))
    context.configure(connection=connection, version_table_schema=schema)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    schema, url = target()
    if url is None:
        raise SystemExit("DATABASE_URL is not set; use the hosted Supabase project (plan.md §3)")
    engine = create_async_engine(url, poolclass=sa.pool.NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(run_sync_migrations, schema)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
