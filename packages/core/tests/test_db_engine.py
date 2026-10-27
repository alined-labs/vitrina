import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.db
async def test_db_art_v_3_the_machine_schema_survives_a_read_only_session(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # A read-only session ends in ROLLBACK; the search_path set on connect must not go with it,
    # or the next session on that pooled connection reads another schema (or none).
    for _ in range(3):
        async with db_sessions() as session:
            schema = await session.scalar(sa.text("SELECT current_schema()"))
            assert schema is not None
            assert schema.startswith("test_")
            await session.scalar(sa.text("SELECT count(*) FROM jobs"))
