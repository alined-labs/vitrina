"""T030 Postgres asset store: one row per file per seller, seller-scoped reads (FR-010, Art. V.3)."""

from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import ProductCreate
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_core.db.tables import sellers
from vitrina_core.ids import new_id
from vitrina_creative.api import AssetRecord, PostgresAssetStore, list_asset_ids

SELLER = UUID("00000000-0000-7000-8000-00000000002a")
OTHER = UUID("00000000-0000-7000-8000-00000000002b")


async def seed(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    async with sessions() as session, session.begin():
        for seller_id, number in ((SELLER, "+573000000021"), (OTHER, "+573000000022")):
            await session.execute(
                sa.insert(sellers).values(
                    id=seller_id,
                    business_name="Tienda",
                    owner_name="Ana",
                    whatsapp_number=number,
                    city="Medellín",
                    consent_prompt_text="¿Te aviso? Responde SÍ",
                )
            )
    product = await PostgresCatalog(sessions).create_product(
        SELLER, ProductCreate(name="Bolso", price_cop=85000)
    )
    return product.id


def record(product_id: UUID, sha: str, seller_id: UUID = SELLER) -> AssetRecord:
    asset_id = new_id()
    return AssetRecord(
        id=asset_id,
        seller_id=seller_id,
        product_id=product_id,
        kind="image",
        storage_key=f"{seller_id}/assets/{asset_id}.png",
        sha256=sha,
        width=512,
        height=512,
    )


@pytest.mark.db
async def test_db_fr_010_assets_are_stored_once_per_file_and_scoped_to_the_seller(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    product_id = await seed(db_sessions)
    store = PostgresAssetStore(db_sessions)
    first = await store.add(record(product_id, "a" * 64))
    duplicate = await store.add(record(product_id, "a" * 64))
    second = await store.add(record(product_id, "b" * 64))
    assert duplicate.id == first.id
    assert (await store.get(SELLER, first.id)) == first
    assert await store.get(OTHER, first.id) is None
    assert await store.by_sha256(OTHER, "a" * 64) is None
    assert await list_asset_ids(store, SELLER, product_id) == [first.id, second.id]
