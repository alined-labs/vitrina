from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from vitrina_catalog.api import (
    FactCreate,
    FactUpdate,
    InMemoryCatalog,
    ProductCreate,
    ProductUpdate,
)

SELLER = UUID("00000000-0000-7000-8000-000000000001")
OTHER = UUID("00000000-0000-7000-8000-000000000002")


@pytest.fixture
def catalog() -> InMemoryCatalog:
    ticks = iter(range(10_000))
    start = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
    return InMemoryCatalog(clock=lambda: start + timedelta(seconds=next(ticks)))


async def test_as_1_2_a_new_product_is_active_and_its_amounts_are_extracted(
    catalog: InMemoryCatalog,
) -> None:
    product = await catalog.create_product(
        SELLER,
        ProductCreate(
            name="Bolso tejido",
            description="Hecho a mano. Con estuche de $5.000",
            price_cop=85000,
            stock=4,
            attributes={"color": "rojo"},
        ),
    )

    assert product.status == "active"
    assert product.source == "manual"
    assert (product.price_cop, product.stock, product.reserved) == (85000, 4, 0)
    assert [citation.amounts_cop for citation in await catalog.citations(SELLER, [product.id])] == [
        (5000, 85000)
    ]
    assert [result.id for result in await catalog.search(SELLER, "bolso rojo")] == [product.id]


async def test_fr_003_draft_products_stay_out_of_retrieval_until_approved(
    catalog: InMemoryCatalog,
) -> None:
    draft = await catalog.create_product(
        SELLER, ProductCreate(name="Torta de chocolate", price_cop=60000, status="draft")
    )
    assert await catalog.search(SELLER, "torta") == []

    approved = await catalog.update_product(SELLER, draft.id, ProductUpdate(status="active"))

    assert approved is not None
    assert approved.status == "active"
    assert [result.id for result in await catalog.search(SELLER, "torta")] == [draft.id]


async def test_updating_the_price_refreshes_the_citable_amounts(catalog: InMemoryCatalog) -> None:
    product = await catalog.create_product(SELLER, ProductCreate(name="Gorra", price_cop=30000))

    await catalog.update_product(SELLER, product.id, ProductUpdate(price_cop=35000, stock=None))

    [citation] = await catalog.citations(SELLER, [product.id])
    assert citation.amounts_cop == (35000,)


async def test_archived_products_leave_the_catalog_but_stay_readable(
    catalog: InMemoryCatalog,
) -> None:
    product = await catalog.create_product(SELLER, ProductCreate(name="Gorra", price_cop=30000))

    assert await catalog.archive_product(SELLER, product.id) is True
    archived = await catalog.product(SELLER, product.id)

    assert archived is not None
    assert archived.status == "archived"
    assert await catalog.search(SELLER, "gorra") == []


async def test_products_page_newest_first_and_filter_by_status_and_text(
    catalog: InMemoryCatalog,
) -> None:
    first = await catalog.create_product(
        SELLER, ProductCreate(name="Bolso tejido", price_cop=85000)
    )
    second = await catalog.create_product(
        SELLER, ProductCreate(name="Gorra", price_cop=30000, status="draft")
    )
    third = await catalog.create_product(
        SELLER, ProductCreate(name="Bolso de cuero", price_cop=120000)
    )
    await catalog.create_product(OTHER, ProductCreate(name="Bolso ajeno", price_cop=1000))

    page, cursor = await catalog.list_products(SELLER, limit=2)
    rest, end = await catalog.list_products(SELLER, limit=2, cursor=cursor)
    drafts, _ = await catalog.list_products(SELLER, limit=25, status="draft")
    bags, _ = await catalog.list_products(SELLER, limit=25, q="bolso")

    assert [p.id for p in page] == [third.id, second.id]
    assert ([p.id for p in rest], end) == ([first.id], None)
    assert [p.id for p in drafts] == [second.id]
    assert {p.id for p in bags} == {first.id, third.id}


async def test_as_1_4_a_shipping_fact_keeps_its_kind_and_amounts(catalog: InMemoryCatalog) -> None:
    fact = await catalog.create_fact(
        SELLER, FactCreate(kind="shipping", text="Envío a Medellín $12.000, 2–3 días")
    )

    assert (fact.kind, fact.status, fact.amounts_cop) == ("shipping", "active", [12000])
    assert [
        result.id for result in await catalog.search(SELLER, "¿cuánto vale el envío a medellin?")
    ] == [fact.id]


async def test_facts_can_be_edited_listed_by_kind_and_deleted(catalog: InMemoryCatalog) -> None:
    fact = await catalog.create_fact(SELLER, FactCreate(kind="faq", text="Abrimos de 9 a 6"))
    await catalog.create_fact(SELLER, FactCreate(kind="shipping", text="Envío a Cali $15.000"))

    updated = await catalog.update_fact(
        SELLER, fact.id, FactUpdate(kind="hours", text="Abrimos de lunes a sábado, 9 a 18")
    )
    hours, _ = await catalog.list_facts(SELLER, limit=25, kind="hours")

    assert updated is not None
    assert (updated.kind, updated.text) == ("hours", "Abrimos de lunes a sábado, 9 a 18")
    assert [f.id for f in hours] == [fact.id]
    assert await catalog.delete_fact(SELLER, fact.id) is True
    assert await catalog.delete_fact(SELLER, fact.id) is False


async def test_art_v_3_crud_never_reaches_another_sellers_rows(catalog: InMemoryCatalog) -> None:
    product = await catalog.create_product(OTHER, ProductCreate(name="Bolso", price_cop=1000))
    fact = await catalog.create_fact(OTHER, FactCreate(kind="faq", text="Hola"))

    assert await catalog.product(SELLER, product.id) is None
    assert await catalog.update_product(SELLER, product.id, ProductUpdate(name="mío")) is None
    assert await catalog.archive_product(SELLER, product.id) is False
    assert await catalog.update_fact(SELLER, fact.id, FactUpdate(text="mío")) is None
    assert await catalog.delete_fact(SELLER, fact.id) is False
