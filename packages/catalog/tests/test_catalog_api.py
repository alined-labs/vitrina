from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from vitrina_catalog.api import Catalog, InMemoryCatalog, ProductNotFoundError

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
SELLER = UUID("00000000-0000-7000-8000-000000000001")
OTHER_SELLER = UUID("00000000-0000-7000-8000-000000000002")
CONVERSATION = UUID("00000000-0000-7000-8000-0000000000c1")


@pytest.fixture
def catalog() -> InMemoryCatalog:
    return InMemoryCatalog(clock=lambda: NOW)


async def test_as_1_4_shipping_fact_is_retrievable_by_search(catalog: InMemoryCatalog) -> None:
    fact_id = catalog.add_fact(
        SELLER, "shipping", "Envío a Medellín $12.000, 2–3 días", amounts_cop=(12000,)
    )
    catalog.add_fact(SELLER, "payment", "Recibimos Nequi y transferencia")

    results = await catalog.search(SELLER, "¿cuánto vale el envío a medellin?")

    assert results[0].id == fact_id
    assert results[0].kind == "shipping"
    assert "Medellín" in results[0].text
    assert [result.kind for result in results] == ["shipping"]


async def test_as_1_4_a_plural_and_its_singular_find_each_other(catalog: InMemoryCatalog) -> None:
    closed = catalog.add_fact(SELLER, "hours", "Los domingos no hay atención")
    bracelet = catalog.add_product(SELLER, "Manilla tejida", price_cop=25000)

    assert [result.id for result in await catalog.search(SELLER, "¿abren el domingo?")] == [closed]
    assert [result.id for result in await catalog.search(SELLER, "manillas")] == [bracelet]


async def test_as_1_4_a_question_that_names_a_kind_finds_its_facts(
    catalog: InMemoryCatalog,
) -> None:
    lunch = catalog.add_fact(SELLER, "hours", "Almuerzos de lunes a viernes, 12 a 3 pm")
    catalog.add_fact(SELLER, "payment", "Recibimos Nequi y transferencia")

    results = await catalog.search(SELLER, "horario sábado")

    assert [result.id for result in results] == [lunch]


async def test_r1_draft_and_archived_items_are_never_returned(catalog: InMemoryCatalog) -> None:
    draft = catalog.add_product(SELLER, "Bolso tejido", price_cop=85000, status="draft")
    catalog.add_product(SELLER, "Bolso de cuero", price_cop=120000, status="archived")
    catalog.add_fact(SELLER, "faq", "Bolsos hechos a mano por encargo", status="draft")

    assert await catalog.search(SELLER, "bolso") == []
    with pytest.raises(ProductNotFoundError):
        await catalog.get_product(SELLER, draft)
    assert await catalog.citations(SELLER, [draft]) == []


async def test_every_call_is_scoped_to_the_seller(catalog: InMemoryCatalog) -> None:
    foreign = catalog.add_product(OTHER_SELLER, "Bolso tejido", price_cop=85000, stock=4)

    assert await catalog.search(SELLER, "bolso tejido") == []
    with pytest.raises(ProductNotFoundError):
        await catalog.get_product(SELLER, foreign)
    with pytest.raises(ProductNotFoundError):
        await catalog.check_stock(SELLER, foreign)
    with pytest.raises(ProductNotFoundError):
        await catalog.reserve(SELLER, CONVERSATION, foreign, quantity=1)
    assert await catalog.citations(SELLER, [foreign]) == []


async def test_product_detail_and_search_result_carry_price_and_stock(
    catalog: InMemoryCatalog,
) -> None:
    product_id = catalog.add_product(
        SELLER,
        "Bolso tejido",
        price_cop=85000,
        stock=5,
        description="Hecho a mano en Medellín",
        attributes={"color": "rojo"},
    )

    detail = await catalog.get_product(SELLER, product_id)
    [result] = await catalog.search(SELLER, "bolso rojo", kinds=["product"])

    assert (detail.name, detail.price_cop, detail.stock, detail.available) == (
        "Bolso tejido",
        85000,
        5,
        5,
    )
    assert detail.attributes == {"color": "rojo"}
    assert detail.status == "active"
    assert (result.id, result.kind, result.price_cop, result.stock) == (
        product_id,
        "product",
        85000,
        5,
    )
    assert "$85.000" in result.text


async def test_search_respects_kinds_and_top_k(catalog: InMemoryCatalog) -> None:
    for city in ("Medellín", "Cali", "Bogotá"):
        catalog.add_fact(SELLER, "shipping", f"Envío a {city} en 2 días")
    catalog.add_product(SELLER, "Envío express", price_cop=15000)

    shipping = await catalog.search(SELLER, "envío", kinds=["shipping"], top_k=2)

    assert len(shipping) == 2
    assert {result.kind for result in shipping} == {"shipping"}


async def test_search_by_image_embedding_ranks_the_closest_product(
    catalog: InMemoryCatalog,
) -> None:
    bag = catalog.add_product(
        SELLER, "Bolso tejido", price_cop=85000, image_embedding=(1.0, 0.0, 0.0)
    )
    catalog.add_product(SELLER, "Gorra", price_cop=30000, image_embedding=(0.0, 1.0, 0.0))

    results = await catalog.search(SELLER, "", kinds=["product"], image_embedding=(0.9, 0.1, 0.0))

    assert results[0].id == bag


async def test_untracked_stock_is_reported_as_not_tracked(catalog: InMemoryCatalog) -> None:
    product_id = catalog.add_product(SELLER, "Torta por encargo", price_cop=60000, stock=None)

    level = await catalog.check_stock(SELLER, product_id)
    reservation = await catalog.reserve(SELLER, CONVERSATION, product_id, quantity=1)

    assert (level.available, level.tracked) == (None, False)
    assert reservation.status == "not_tracked"
    assert reservation.reservation_id is None


async def test_as_3_7_reserve_holds_stock_for_24_hours(catalog: InMemoryCatalog) -> None:
    product_id = catalog.add_product(SELLER, "Bolso tejido", price_cop=85000, stock=3)

    reservation = await catalog.reserve(SELLER, CONVERSATION, product_id, quantity=2)
    level = await catalog.check_stock(SELLER, product_id)
    too_many = await catalog.reserve(SELLER, CONVERSATION, product_id, quantity=2)

    assert reservation.status == "reserved"
    assert reservation.reservation_id is not None
    assert reservation.code is not None
    assert reservation.code.startswith("R-")
    assert len(reservation.code) == 6
    assert reservation.expires_at == NOW + timedelta(hours=24)
    assert level.available == 1
    assert too_many.status == "insufficient_stock"


async def test_expired_reservations_release_stock() -> None:
    now = [NOW]
    catalog = InMemoryCatalog(clock=lambda: now[0])
    product_id = catalog.add_product(SELLER, "Bolso tejido", price_cop=85000, stock=1)
    await catalog.reserve(SELLER, CONVERSATION, product_id, quantity=1)

    now[0] = NOW + timedelta(hours=24, seconds=1)

    assert (await catalog.check_stock(SELLER, product_id)).available == 1


async def test_citations_return_amounts_of_active_items_only(catalog: InMemoryCatalog) -> None:
    fact = catalog.add_fact(SELLER, "shipping", "Envío a Medellín $12.000", amounts_cop=(12000,))
    product = catalog.add_product(
        SELLER, "Bolso tejido", price_cop=85000, amounts_cop=(85000, 5000)
    )

    citations = {
        citation.id: citation
        for citation in await catalog.citations(SELLER, [fact, product, uuid4()])
    }

    assert set(citations) == {fact, product}
    assert citations[fact].amounts_cop == (12000,)
    assert citations[product].amounts_cop == (5000, 85000)


async def test_product_amounts_always_include_the_price(catalog: InMemoryCatalog) -> None:
    product = catalog.add_product(SELLER, "Gorra", price_cop=30000)

    [citation] = await catalog.citations(SELLER, [product])

    assert citation.amounts_cop == (30000,)


def test_in_memory_catalog_implements_the_protocol(catalog: InMemoryCatalog) -> None:
    implementation: Catalog = catalog

    assert implementation is catalog
