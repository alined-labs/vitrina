"""Test support for database tests in any package (the fixtures live in the root conftest.py).

Database tests run against a Postgres with pgvector, never a local one on the 8 GB machines:
CI's service container, or the Supabase project when DATABASE_URL is set. Each test gets a
throwaway schema migrated to head.
"""

import asyncio
import io
import os
import re

import asyncpg
from alembic import command
from alembic.config import Config

from vitrina_core.db import search_path
from vitrina_core.settings import REPO_ROOT


class DatabaseUnavailableError(Exception):
    """No DATABASE_URL: the caller skips, unless CI_DB_REQUIRED makes it a failure."""


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    raise DatabaseUnavailableError(
        "DATABASE_URL is not set (CI runs database tests against its Postgres service)"
    )


def database_required() -> bool:
    return bool(os.environ.get("CI_DB_REQUIRED"))


def alembic_config(schema: str, url: str | None = None) -> tuple[Config, io.StringIO]:
    """An Alembic config aimed at ``schema``; offline SQL goes to the returned buffer."""
    buffer = io.StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=buffer)
    config.attributes["schema"] = schema
    if url is not None:
        config.attributes["url"] = url
    return config, buffer


def plain_url(url: str) -> str:
    """The URL asyncpg itself accepts (no SQLAlchemy driver suffix)."""
    return re.sub(r"^postgres(ql)?(\+asyncpg)?://", "postgresql://", url)


def migrate(url: str, schema: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(schema, url)[0], revision)


async def connect(url: str, schema: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        plain_url(url), server_settings={"search_path": search_path(schema)}
    )


def drop_schema(url: str, schema: str) -> None:
    async def drop() -> None:
        connection = await asyncpg.connect(plain_url(url))
        try:
            await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await connection.close()

    asyncio.run(drop())
