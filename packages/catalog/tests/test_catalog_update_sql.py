"""What ``PostgresCatalog.update_product`` sends to the database, checked without one."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import ProductUpdate
from vitrina_catalog.postgres import PostgresCatalog

SELLER = UUID("00000000-0000-7000-8000-000000000001")
PRODUCT = UUID("00000000-0000-7000-8000-000000000002")
ROW = {
    "id": PRODUCT,
    "seller_id": SELLER,
    "name": "Gorra",
    "description": None,
    "price_cop": 30000,
    "stock": 2,
    "attributes": {},
    "status": "active",
    "source": "manual",
    "amounts_cop": [30000],
    "created_at": datetime(2026, 10, 28, tzinfo=UTC),
    "updated_at": datetime(2026, 10, 28, tzinfo=UTC),
}


class Result:
    def mappings(self) -> Result:
        return self

    def first(self) -> dict[str, Any]:
        return ROW

    def one(self) -> dict[str, Any]:
        return ROW


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Result:
        self.statements.append(statement)
        return Result()

    async def scalar(self, _statement: Any) -> int:
        return 1

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


def recording_catalog() -> tuple[PostgresCatalog, RecordingSession]:
    session = RecordingSession()

    @asynccontextmanager
    async def sessions() -> AsyncIterator[RecordingSession]:
        yield session

    return PostgresCatalog(cast("async_sessionmaker[AsyncSession]", sessions)), session


async def test_an_empty_product_update_sends_no_update_statement() -> None:
    catalog, session = recording_catalog()

    product = await catalog.update_product(SELLER, PRODUCT, ProductUpdate())

    assert product is not None
    assert (product.name, product.price_cop, product.reserved) == ("Gorra", 30000, 1)
    assert not [item for item in session.statements if isinstance(item, sa.Update)]


async def test_a_real_change_still_sends_its_update() -> None:
    catalog, session = recording_catalog()

    await catalog.update_product(SELLER, PRODUCT, ProductUpdate(stock=5))

    assert [item for item in session.statements if isinstance(item, sa.Update)]
