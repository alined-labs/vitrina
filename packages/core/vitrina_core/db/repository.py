"""Seller scoping for every query (constitution Art. V.3; data-model.md preamble).

Subclasses set ``table``; every statement they build is filtered by the repository's
``seller_id`` and every insert carries it. A cross-seller read is a severity-one bug, so
there is no way to build an unscoped statement through this class.
"""

from collections.abc import Mapping
from typing import Any, ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from vitrina_core.ids import new_id

# SQLAlchemy 2.1 types Select over a TypeVarTuple; Select[Any] would collapse to Any.
type AnySelect = sa.Select[*tuple[Any, ...]]


class SellerScopedRepository:
    table: ClassVar[sa.Table]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        table = cls.__dict__.get("table")
        if table is not None and ("seller_id" not in table.c or "id" not in table.c):
            raise TypeError(f"{cls.__name__}.table must have id and seller_id columns")

    def __init__(self, session: AsyncSession, seller_id: UUID) -> None:
        self.session = session
        self.seller_id = seller_id

    def _owned(self) -> sa.ColumnElement[bool]:
        return self.table.c.seller_id == self.seller_id

    def _check_values(self, values: Mapping[str, Any]) -> None:
        if "seller_id" in values and values["seller_id"] != self.seller_id:
            raise ValueError(
                "seller_id is fixed by the repository; another seller's rows are out of reach"
            )

    # ── statements ──

    def select(self, *columns: sa.ColumnElement[Any]) -> AnySelect:
        return sa.select(*(columns or (self.table,))).where(self._owned())

    def get_statement(self, row_id: UUID) -> AnySelect:
        return self.select().where(self.table.c.id == row_id)

    def insert_statement(self, values: Mapping[str, Any]) -> sa.Insert:
        self._check_values(values)
        row = {"id": new_id(), **values, "seller_id": self.seller_id}
        return sa.insert(self.table).values(row).returning(*self.table.c)

    def update_statement(self, row_id: UUID, values: Mapping[str, Any]) -> sa.Update:
        self._check_values(values)
        changes = {key: value for key, value in values.items() if key != "seller_id"}
        return (
            sa.update(self.table)
            .where(self._owned(), self.table.c.id == row_id)
            .values(changes)
            .returning(*self.table.c)
        )

    def delete_statement(self, row_id: UUID) -> sa.Delete:
        return sa.delete(self.table).where(self._owned(), self.table.c.id == row_id)

    # ── execution ──

    async def get(self, row_id: UUID) -> sa.RowMapping | None:
        result = await self.session.execute(self.get_statement(row_id))
        return result.mappings().one_or_none()

    async def insert(self, values: Mapping[str, Any]) -> sa.RowMapping:
        result = await self.session.execute(self.insert_statement(values))
        return result.mappings().one()

    async def update(self, row_id: UUID, values: Mapping[str, Any]) -> sa.RowMapping | None:
        result = await self.session.execute(self.update_statement(row_id, values))
        return result.mappings().one_or_none()

    async def delete(self, row_id: UUID) -> bool:
        result = await self.session.execute(self.delete_statement(row_id))
        return bool(getattr(result, "rowcount", 0))
