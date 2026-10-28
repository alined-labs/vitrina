"""Fixtures for the learning module's tests and for recording its cassettes (T044, T070).

``fixture_dataset`` is a synthetic period of one business: replaying the recorded Wonder
call needs this exact dataset, so change it only together with the cassette.
"""

from datetime import date
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from vitrina_core.ids import new_id
from vitrina_learning.briefs import AngleTotals, BriefDataset, DatasetAd, DatasetProduct

BAG = UUID("0192f310-0a1b-7c2d-8e3f-4a5b6c7d8e01")
WALLET = UUID("0192f310-0a1b-7c2d-8e3f-4a5b6c7d8e02")
HANDMADE_AD = UUID("0192f310-0a1b-7c2d-8e3f-4a5b6c7d8e11")
PRICE_AD = UUID("0192f310-0a1b-7c2d-8e3f-4a5b6c7d8e12")
NOT_OURS = UUID("0192f310-0a1b-7c2d-8e3f-4a5b6c7d8eff")


def fixture_dataset() -> BriefDataset:
    """ "Hecho a mano" brings three times the chats of "precio", and shipping holds it back."""
    return BriefDataset(
        business_name="Mochilas Ana",
        period_from=date(2026, 10, 1),
        period_to=date(2026, 10, 28),
        attributed_chats=16,
        ads=[
            DatasetAd(
                ad_id=HANDMADE_AD,
                code="A7",
                angle="handmade",
                chats=12,
                reservations=3,
                bookings=0,
                handoffs=1,
                conversion_rate=0.25,
                objections_by_tag={"price": 1, "shipping": 5},
            ),
            DatasetAd(
                ad_id=PRICE_AD,
                code="A8",
                angle="price",
                chats=4,
                reservations=1,
                bookings=0,
                handoffs=0,
                conversion_rate=0.25,
                objections_by_tag={"shipping": 1},
            ),
        ],
        by_angle={
            "handmade": AngleTotals(chats=12, conversion_rate=0.25),
            "price": AngleTotals(chats=4, conversion_rate=0.25),
        },
        objections={"price": 2, "shipping": 6},
        products=[
            DatasetProduct(id=WALLET, name="Bolso de cuero café", price_cop=145000),
            DatasetProduct(id=BAG, name="Mochila wayuu azul", price_cop=85000),
        ],
        ads_by_status={"approved": 2, "draft": 1},
    )


# ── Postgres fixture rows (synthetic; the baseline's own constraints and triggers apply) ──


async def approved_ad(session: AsyncSession, seller: UUID, number: str) -> UUID:
    """A seller with one approved ad #A7 (angle price) over a passed creative (fixture rows)."""
    product, job, creative, variant, ad = (new_id() for _ in range(5))
    statements = [
        (
            "INSERT INTO sellers (id, business_name, owner_name, whatsapp_number, city,"
            " consent_prompt_text) VALUES (:seller, 'Tienda', 'Ana', :number, 'Medellín', '¿Sí?')",
            {"number": number},
        ),
        (
            "INSERT INTO products (id, seller_id, name, price_cop)"
            " VALUES (:product, :seller, 'Mochila azul', 85000)",
            {},
        ),
        (
            "INSERT INTO creative_jobs (id, seller_id, product_id, status, stage)"
            " VALUES (:job, :seller, :product, 'succeeded', 'ready')",
            {},
        ),
        (
            "INSERT INTO creatives (id, job_id, seller_id, product_id, format, verification)"
            " VALUES (:creative, :job, :seller, :product, 'catalog_1x1', '{\"status\": \"passed\"}')",
            {},
        ),
        (
            "INSERT INTO copy_variants (id, job_id, seller_id, hook, angle, text)"
            " VALUES (:variant, :job, :seller, 'question', 'price', '¿Buscas una mochila?')",
            {},
        ),
        (
            "INSERT INTO ads (id, seller_id, product_id, creative_id, copy_variant_id, code,"
            " angle, status) VALUES (:ad, :seller, :product, :creative, :variant, 'A7', 'price',"
            " 'approved')",
            {},
        ),
    ]
    ids = {"seller": seller, "product": product, "job": job, "creative": creative}
    ids |= {"variant": variant, "ad": ad}
    for statement, extra in statements:
        await session.execute(sa.text(statement), ids | extra)
    return ad


async def conversation(session: AsyncSession, seller: UUID) -> UUID:
    contact, chat = new_id(), new_id()
    await session.execute(
        sa.text("INSERT INTO contacts (id, seller_id, wa_id_hash) VALUES (:id, :seller, :hash)"),
        {"id": contact, "seller": seller, "hash": contact.bytes},
    )
    await session.execute(
        sa.text(
            "INSERT INTO conversations (id, seller_id, contact_id) VALUES (:id, :seller, :contact)"
        ),
        {"id": chat, "seller": seller, "contact": contact},
    )
    return chat
