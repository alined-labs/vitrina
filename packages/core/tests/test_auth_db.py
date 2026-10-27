from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.auth import PostgresSellerDirectory
from vitrina_core.db.tables import sellers

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
SUBJECT = "6f1c2a64-3c2e-4d55-9a1c-5c0d1e2f3a4b"


@pytest.mark.db
async def test_db_sellers_are_found_by_their_supabase_subject(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.insert(sellers).values(
                id=SELLER,
                business_name="Bolsos Ana",
                owner_name="Ana",
                whatsapp_number="+573000000001",
                city="Medellín",
                consent_prompt_text="¿Te aviso cuando haya promo? Responde SÍ",
                auth_subject=SUBJECT,
            )
        )
    directory = PostgresSellerDirectory(db_sessions)

    assert await directory.seller_for_subject(SUBJECT) == SELLER
    assert await directory.seller_for_subject("11111111-2222-4333-8444-555555555555") is None
