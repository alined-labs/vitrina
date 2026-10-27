"""T052 bookings on Postgres (CI's db job): the per-seller advisory lock keeps one appointment
per slot even when two chats ask at once, and every query is scoped to the seller (Art. V.3).
"""

import asyncio
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import SellerSettings
from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_conversation.api import PostgresBookings, PostgresConversationStore
from vitrina_conversation.booking import SlotRequest, book_slot
from vitrina_conversation.tables import bookings

pytestmark = pytest.mark.db

BOGOTA = ZoneInfo("America/Bogota")
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=BOGOTA)
SATURDAY_10AM = datetime(2026, 10, 31, 10, 0, tzinfo=BOGOTA)


async def open_seller(
    sessions: async_sessionmaker[AsyncSession], number: str
) -> tuple[SellerSettings, list[UUID]]:
    """A barbershop open on Saturday mornings, with two customer conversations."""
    store = PostgresSellerStore(sessions)
    seller = await store.create(
        SellerCreate(
            business_name="Barbería Ana",
            owner_name="Ana",
            whatsapp_number=number,
            city="Medellín",
            category="services",
        ),
        auth_subject=None,
    )
    settings = await store.settings(seller.id)
    assert settings is not None
    identity = ContactIdentity(
        pepper=SecretStr("booking-db-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(sessions, identity)
    conversations = PostgresConversationStore(sessions)
    opened = []
    for customer in ("573000000001", "573000000002"):
        contact = await compliance.resolve_contact(seller.id, customer)
        opened.append((await conversations.open_conversation(seller.id, contact.id)).id)
    hours = {"sat": [["09:00", "13:00"]]}
    return settings.model_copy(update={"business_hours": hours}), opened


async def test_db_t052_two_chats_racing_for_one_slot_get_it_once(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller, conversations = await open_seller(db_sessions, "+573000000101")
    store = PostgresBookings(db_sessions)
    slot = SlotRequest("Corte", SATURDAY_10AM, 30, "Laura")

    results = await asyncio.gather(
        *(book_slot(store, seller, chat, slot, now=NOW) for chat in conversations)
    )

    assert sorted(result.status for result in results) == ["booked", "slot_taken"]
    refused = next(result for result in results if result.status == "slot_taken")
    assert refused.alternatives[0] == datetime(2026, 10, 31, 10, 30, tzinfo=BOGOTA)
    async with db_sessions() as session:
        rows = (
            await session.execute(
                sa.select(bookings).where(bookings.c.seller_id == seller.seller_id)
            )
        ).all()
    (row,) = rows
    assert (row.service, row.starts_at, row.duration_min, row.status) == (
        "Corte",
        SATURDAY_10AM,
        30,
        "booked",
    )


async def test_db_t052_another_sellers_booking_leaves_the_slot_free(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first, first_chats = await open_seller(db_sessions, "+573000000102")
    second, second_chats = await open_seller(db_sessions, "+573000000103")
    store = PostgresBookings(db_sessions)
    slot = SlotRequest("Corte", SATURDAY_10AM, 30)

    await book_slot(store, first, first_chats[0], slot, now=NOW)
    result = await book_slot(store, second, second_chats[0], slot, now=NOW)

    assert result.status == "booked"


async def test_db_t052_overlaps_count_the_whole_appointment(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller, conversations = await open_seller(db_sessions, "+573000000104")
    store = PostgresBookings(db_sessions)
    await book_slot(
        store, seller, conversations[0], SlotRequest("Tinte", SATURDAY_10AM, 90), now=NOW
    )

    inside = await book_slot(
        store,
        seller,
        conversations[1],
        SlotRequest("Corte", datetime(2026, 10, 31, 11, 0, tzinfo=BOGOTA), 30),
        now=NOW,
    )
    after = await book_slot(
        store,
        seller,
        conversations[1],
        SlotRequest("Corte", datetime(2026, 10, 31, 11, 30, tzinfo=BOGOTA), 30),
        now=NOW,
    )

    assert (inside.status, after.status) == ("slot_taken", "booked")
