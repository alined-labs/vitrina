"""Product search reads the same fields in Postgres as in the store the evals use."""

import re

from alembic import command

from vitrina_catalog.memory import InMemoryCatalog
from vitrina_catalog.schemas import ProductCreate
from vitrina_catalog.tables import products
from vitrina_core.ids import new_id
from vitrina_core.testing import alembic_config


def head_sql() -> str:
    config, buffer = alembic_config("ci_offline")
    command.upgrade(config, "head", sql=True)
    return buffer.getvalue()


def test_the_table_mirror_generates_search_as_the_last_migration_does() -> None:
    computed = products.c.search_tsv.computed
    assert computed is not None

    columns = re.findall(
        r"ALTER TABLE products ADD COLUMN search_tsv TSVECTOR GENERATED ALWAYS AS \((.+)\) STORED;",
        head_sql(),
    )

    assert columns[-1] == str(computed.sqltext)


def test_postgres_search_text_holds_every_field_the_evals_store_searches() -> None:
    expression = str(products.c.search_tsv.computed.sqltext)  # type: ignore[union-attr]

    for column in ("name", "description", "price_cop", "attributes"):
        assert re.search(rf"\b{column}\b", expression), column


async def test_the_evals_store_finds_a_product_by_any_of_those_fields() -> None:
    seller = new_id()
    catalog = InMemoryCatalog()
    bag = await catalog.create_product(
        seller,
        ProductCreate(
            name="Bolso tejido",
            description="Hecho artesanal",
            price_cop=85000,
            attributes={"color": "Rojo"},
        ),
    )

    for question in ("tejido", "artesanal", "precio", "$85.000", "color", "rojo"):
        assert [hit.id for hit in await catalog.search(seller, question)] == [bag.id], question
