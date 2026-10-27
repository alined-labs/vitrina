from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from vitrina_core.db import SellerScopedRepository

SELLER = UUID("00000000-0000-7000-8000-000000000001")
ROW = UUID("00000000-0000-7000-8000-0000000000aa")
metadata = sa.MetaData()

things = sa.Table(
    "test_things",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("seller_id", sa.Uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)


class Things(SellerScopedRepository):
    table = things


def compiled(statement: Any) -> tuple[str, dict[str, Any]]:
    result = statement.compile(dialect=postgresql.dialect())
    return str(result), dict(result.params)


def repository() -> Things:
    return Things(session=None, seller_id=SELLER)  # type: ignore[arg-type]


def test_art_v_3_every_read_is_filtered_by_seller() -> None:
    sql, params = compiled(repository().select().where(things.c.name == "bolso"))

    assert "test_things.seller_id = %(seller_id_1)s" in sql
    assert params["seller_id_1"] == SELLER


def test_art_v_3_get_update_and_delete_are_filtered_by_seller_and_id() -> None:
    repo = repository()

    for statement in (
        repo.get_statement(ROW),
        repo.update_statement(ROW, {"name": "gorra"}),
        repo.delete_statement(ROW),
    ):
        sql, params = compiled(statement)
        assert "test_things.seller_id = %(seller_id_1)s" in sql
        assert "test_things.id = %(id_1)s" in sql
        assert (params["seller_id_1"], params["id_1"]) == (SELLER, ROW)


def test_inserts_always_carry_the_repository_seller_and_a_time_ordered_id() -> None:
    sql, params = compiled(repository().insert_statement({"name": "bolso"}))

    assert "INSERT INTO test_things" in sql
    assert params["seller_id"] == SELLER
    assert isinstance(params["id"], UUID)
    assert params["id"].version == 7


def test_a_repository_refuses_to_write_another_sellers_rows() -> None:
    other = UUID("00000000-0000-7000-8000-000000000002")

    with pytest.raises(ValueError, match="seller_id"):
        repository().insert_statement({"name": "bolso", "seller_id": other})
    with pytest.raises(ValueError, match="seller_id"):
        repository().update_statement(ROW, {"seller_id": other})


def test_a_table_without_seller_id_cannot_be_scoped() -> None:
    unscoped = sa.Table("test_unscoped", metadata, sa.Column("id", sa.Uuid, primary_key=True))

    with pytest.raises(TypeError, match="seller_id"):

        class Unscoped(SellerScopedRepository):
            table = unscoped
