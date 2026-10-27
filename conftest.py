"""Fixtures shared by every package's tests."""

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core import testing
from vitrina_core.db import create_engine, create_session_factory
from vitrina_core.settings import Settings


@pytest.fixture
def fresh_schema() -> Iterator[tuple[str, str]]:
    """``(url, schema)``: an empty throwaway schema on the test database, dropped afterwards."""
    try:
        url = testing.database_url()
    except testing.DatabaseUnavailableError as error:
        if testing.database_required():
            pytest.fail(f"CI_DB_REQUIRED is set but {error}")
        pytest.skip(str(error))
    schema = f"test_{uuid4().hex[:12]}"
    yield url, schema
    testing.drop_schema(url, schema)


@pytest.fixture
def migrated_schema(fresh_schema: tuple[str, str]) -> tuple[str, str]:
    """``(url, schema)`` migrated to head."""
    testing.migrate(*fresh_schema)
    return fresh_schema


@pytest.fixture
async def db_sessions(
    migrated_schema: tuple[str, str],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory bound to the migrated schema."""
    url, schema = migrated_schema
    engine = create_engine(Settings(_env_file=None, database_url=url, db_schema=schema))  # type: ignore[call-arg]
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
