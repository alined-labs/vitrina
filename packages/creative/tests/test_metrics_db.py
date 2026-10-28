"""T073 live counter: scenes the verifier blocked, per seller (FR-070, AS-7.1)."""

from datetime import UTC, datetime
from importlib.metadata import entry_points
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import ProductCreate
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_core.db.tables import sellers
from vitrina_core.ids import new_id
from vitrina_creative.metrics import counters
from vitrina_creative.tables import creative_jobs, creatives

SELLER = UUID("00000000-0000-7000-8000-00000000003a")
OTHER = UUID("00000000-0000-7000-8000-00000000003b")


def test_the_counter_is_registered_for_the_metrics_summary() -> None:
    names = {point.name: point.value for point in entry_points(group="vitrina.metrics")}
    assert names.get("creative") == "vitrina_creative.metrics:counters"


async def scenes(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID, *statuses: str
) -> None:
    product = await PostgresCatalog(sessions).create_product(
        seller_id, ProductCreate(name="Bolso", price_cop=85000)
    )
    job_id = new_id()
    async with sessions() as session, session.begin():
        await session.execute(
            sa.insert(creative_jobs).values(
                id=job_id,
                seller_id=seller_id,
                product_id=product.id,
                status="succeeded",
                stage="ready",
                progress=1,
            )
        )
        for status in statuses:
            await session.execute(
                sa.insert(creatives).values(
                    id=new_id(),
                    job_id=job_id,
                    seller_id=seller_id,
                    product_id=product.id,
                    format="catalog_1x1",
                    verification={"status": status},
                )
            )


@pytest.mark.db
async def test_db_as_7_1_blocked_scenes_are_counted_for_the_seller_only(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessions() as session, session.begin():
        for seller_id, number in ((SELLER, "+573000000031"), (OTHER, "+573000000032")):
            await session.execute(
                sa.insert(sellers).values(
                    id=seller_id,
                    business_name="Tienda",
                    owner_name="Ana",
                    whatsapp_number=number,
                    city="Cali",
                    consent_prompt_text="¿Te aviso? Responde SÍ",
                )
            )
    await scenes(db_sessions, SELLER, "passed", "blocked", "blocked", "pending")
    await scenes(db_sessions, OTHER, "blocked")

    counted = await counters(db_sessions, SELLER, datetime(2026, 10, 28, 5, tzinfo=UTC))

    assert counted == {"creatives_blocked_total": 2}
