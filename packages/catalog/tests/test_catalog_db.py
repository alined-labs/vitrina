import asyncio
import hashlib
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import (
    FactCreate,
    ProductCreate,
    ProductNotFoundError,
    ProductUpdate,
    expire_reservations,
)
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_catalog.tables import reservations
from vitrina_catalog.text import tokens
from vitrina_core.db.tables import sellers
from vitrina_core.ids import new_id
from vitrina_core.providers import CallMeta, EmbeddingRequest, EmbeddingResponse

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")


class HashingEmbedder:
    """Deterministic bag-of-words vectors: shared words mean similar vectors."""

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        vectors = []
        for text in request.texts:
            vector = [0.0] * 1024
            for word in tokens(text):
                vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 1024] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append(tuple(v / norm for v in vector))
        return EmbeddingResponse(
            vectors=tuple(vectors),
            meta=CallMeta(provider="local_mock", model=request.model, purpose="embed"),
        )


async def setup(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    """Two sellers and one conversation for reservations."""
    async with sessions() as session, session.begin():
        for seller_id, number in ((SELLER, "+573000000001"), (OTHER, "+573000000002")):
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
        contact, conversation = new_id(), new_id()
        await session.execute(
            sa.text(
                "INSERT INTO contacts (id, seller_id, wa_id_hash) VALUES (:id, :seller, :hash)"
            ),
            {"id": contact, "seller": SELLER, "hash": b"synthetic-contact"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO conversations (id, seller_id, contact_id) VALUES (:id, :seller, :contact)"
            ),
            {"id": conversation, "seller": SELLER, "contact": contact},
        )
    return conversation


@pytest.mark.db
async def test_db_as_1_4_facts_match_questions_without_accents_and_with_any_word(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions)
    shipping = await catalog.create_fact(
        SELLER, FactCreate(kind="shipping", text="Envío a Medellín $12.000, 2–3 días")
    )
    await catalog.create_fact(
        SELLER, FactCreate(kind="payment", text="Recibimos Nequi y transferencia")
    )
    await catalog.create_fact(OTHER, FactCreate(kind="shipping", text="Envío a Medellín $9.000"))

    results = await catalog.search(SELLER, "¿cuánto vale el envio a medellin?")

    assert [result.id for result in results] == [shipping.id]
    assert results[0].kind == "shipping"
    assert [c.amounts_cop for c in await catalog.citations(SELLER, [shipping.id])] == [(12000,)]


@pytest.mark.db
async def test_db_as_1_4_a_question_that_names_a_kind_finds_its_facts(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions)
    lunch = await catalog.create_fact(
        SELLER, FactCreate(kind="hours", text="Almuerzos de lunes a viernes, 12 a 3 pm")
    )
    await catalog.create_fact(
        SELLER, FactCreate(kind="payment", text="Recibimos Nequi y transferencia")
    )
    await catalog.create_fact(OTHER, FactCreate(kind="hours", text="Abrimos todos los días"))

    results = await catalog.search(SELLER, "¿cuál es el horario del sábado?")

    assert [result.id for result in results] == [lunch.id]


@pytest.mark.db
async def test_db_fr_003_drafts_are_not_retrievable_or_citable_until_approved(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions)
    draft = await catalog.create_product(
        SELLER, ProductCreate(name="Torta de chocolate", price_cop=60000, status="draft")
    )

    assert await catalog.search(SELLER, "torta") == []
    assert await catalog.citations(SELLER, [draft.id]) == []
    with pytest.raises(ProductNotFoundError):
        await catalog.get_product(SELLER, draft.id)

    await catalog.update_product(SELLER, draft.id, ProductUpdate(status="active"))
    [found] = await catalog.search(SELLER, "torta de chocolate")
    assert (found.id, found.price_cop) == (draft.id, 60000)
    assert "$60.000" in found.text


@pytest.mark.db
async def test_db_vector_search_finds_products_by_meaning(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions, embedder=HashingEmbedder(), embed_model="hashing")
    bag = await catalog.create_product(
        SELLER,
        ProductCreate(
            name="Bolso tejido", description="Hecho a mano en Medellín", price_cop=85000, stock=3
        ),
    )
    await catalog.create_product(
        SELLER, ProductCreate(name="Gorra", description="Algodón", price_cop=30000)
    )

    results = await catalog.search(SELLER, "busco algo hecho a mano", kinds=["product"])

    assert results[0].id == bag.id
    assert results[0].stock == 3


@pytest.mark.db
async def test_db_as_3_7_concurrent_reservations_never_oversell(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    conversation = await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions)
    last = await catalog.create_product(
        SELLER, ProductCreate(name="Bolso tejido", price_cop=85000, stock=1)
    )

    first, second = await asyncio.gather(
        catalog.reserve(SELLER, conversation, last.id, 1),
        catalog.reserve(SELLER, conversation, last.id, 1),
    )

    assert sorted([first.status, second.status]) == ["insufficient_stock", "reserved"]
    level = await catalog.check_stock(SELLER, last.id)
    assert (level.available, level.tracked) == (0, True)
    product = await catalog.product(SELLER, last.id)
    assert product is not None
    assert product.reserved == 1


@pytest.mark.db
async def test_db_products_page_filter_update_and_archive(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions)
    names = ["Bolso tejido", "Gorra", "Bolso de cuero"]
    created = [
        await catalog.create_product(SELLER, ProductCreate(name=name, price_cop=10000))
        for name in names
    ]

    first, cursor = await catalog.list_products(SELLER, limit=2)
    rest, end = await catalog.list_products(SELLER, limit=2, cursor=cursor)
    bags, _ = await catalog.list_products(SELLER, limit=25, q="bolsos")
    updated = await catalog.update_product(
        SELLER, created[1].id, ProductUpdate(price_cop=12000, description="con visera $2.000")
    )

    assert [p.name for p in first] == ["Bolso de cuero", "Gorra"]
    assert ([p.name for p in rest], end) == (["Bolso tejido"], None)
    assert {p.name for p in bags} == {"Bolso tejido", "Bolso de cuero"}
    assert updated is not None
    assert [c.amounts_cop for c in await catalog.citations(SELLER, [created[1].id])] == [
        (2000, 12000)
    ]
    assert await catalog.archive_product(OTHER, created[0].id) is False
    assert await catalog.archive_product(SELLER, created[0].id) is True
    assert await catalog.search(SELLER, "bolso tejido", kinds=["product"]) != []
    assert all(
        result.id != created[0].id for result in await catalog.search(SELLER, "bolso tejido")
    )


@pytest.mark.db
async def test_db_as_3_7_reservations_past_their_expiry_are_marked_expired(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    conversation = await setup(db_sessions)
    catalog = PostgresCatalog(db_sessions)
    bag = await catalog.create_product(
        SELLER, ProductCreate(name="Bolso tejido", price_cop=85000, stock=3)
    )
    old = await catalog.reserve(SELLER, conversation, bag.id, 1)
    fresh = await catalog.reserve(SELLER, conversation, bag.id, 1)
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.update(reservations)
            .where(reservations.c.id == old.reservation_id)
            .values(expires_at=sa.func.now() - timedelta(hours=1))
        )

    assert await expire_reservations(db_sessions, datetime.now(UTC)) == 1
    assert await expire_reservations(db_sessions, datetime.now(UTC)) == 0

    async with db_sessions() as session:
        rows = (await session.execute(sa.select(reservations.c.id, reservations.c.status))).all()
    assert dict(rows) == {old.reservation_id: "expired", fresh.reservation_id: "reserved"}
    assert (await catalog.check_stock(SELLER, bag.id)).available == 2
