"""T052 ``book_slot``: appointments inside the seller's hours, one at a time (Q37, assumed).

The seeded shop opens Monday 09:00–18:00 and Saturday 09:00–13:00, America/Bogota. Now is
Tuesday 2026-10-27 08:00 there, so the next open days are Saturday 31 and Monday 2 November.
A refused slot comes back with up to three free ones of the same length, on a 30-minute grid.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from vitrina_conversation.booking import Booking, InMemoryBookings, SlotRequest, book_slot
from vitrina_conversation.testing import Shop

BOGOTA = ZoneInfo("America/Bogota")
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=BOGOTA)
CONVERSATION = uuid4()


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=BOGOTA)


def slot(starts_at: datetime, minutes: int = 30) -> SlotRequest:
    return SlotRequest(
        service="Corte", starts_at=starts_at, duration_min=minutes, customer_name="Laura"
    )


async def test_as_3_7_a_slot_inside_the_hours_is_booked(shop: Shop) -> None:
    store = InMemoryBookings()

    result = await book_slot(store, shop.seller, CONVERSATION, slot(at(31, 10)), now=NOW)

    assert result.status == "booked"
    assert result.booking_id is not None
    assert result.alternatives == ()
    (booking,) = store.bookings
    assert (booking.seller_id, booking.conversation_id, booking.starts_at) == (
        shop.seller.seller_id,
        CONVERSATION,
        at(31, 10),
    )


async def test_t052_a_slot_that_runs_past_closing_is_outside_the_hours(shop: Shop) -> None:
    result = await book_slot(
        InMemoryBookings(), shop.seller, CONVERSATION, slot(at(31, 12, 45)), now=NOW
    )

    assert result.status == "outside_hours"
    assert result.alternatives == (at(2, 9, month=11), at(2, 9, 30, month=11), at(2, 10, month=11))


async def test_t052_a_closed_day_offers_the_next_open_one(shop: Shop) -> None:
    result = await book_slot(
        InMemoryBookings(), shop.seller, CONVERSATION, slot(at(1, 10, month=11)), now=NOW
    )

    assert (result.status, result.alternatives[0]) == ("outside_hours", at(2, 9, month=11))


async def test_t052_an_overlapping_slot_is_taken(shop: Shop) -> None:
    store = InMemoryBookings()
    await book_slot(store, shop.seller, uuid4(), slot(at(31, 10)), now=NOW)

    result = await book_slot(store, shop.seller, CONVERSATION, slot(at(31, 10, 15)), now=NOW)

    assert result.status == "slot_taken"
    assert result.alternatives == (at(31, 10, 30), at(31, 11), at(31, 11, 30))
    assert len(store.bookings) == 1


async def test_t052_back_to_back_slots_both_fit(shop: Shop) -> None:
    store = InMemoryBookings()
    await book_slot(store, shop.seller, uuid4(), slot(at(31, 10)), now=NOW)

    result = await book_slot(store, shop.seller, CONVERSATION, slot(at(31, 10, 30)), now=NOW)

    assert result.status == "booked"


async def test_t052_a_slot_in_the_past_offers_times_from_now(shop: Shop) -> None:
    now = at(31, 11)

    result = await book_slot(
        InMemoryBookings(), shop.seller, CONVERSATION, slot(at(31, 10)), now=now
    )

    assert result.status == "outside_hours"
    assert result.alternatives == (at(31, 11), at(31, 11, 30), at(31, 12))


async def test_t052_a_time_without_offset_is_the_sellers_local_time(shop: Shop) -> None:
    store = InMemoryBookings()

    naive = datetime(2026, 10, 31, 10, 0)  # noqa: DTZ001 - the model may send no offset
    result = await book_slot(store, shop.seller, CONVERSATION, slot(naive), now=NOW)

    assert result.status == "booked"
    (booking,) = store.bookings
    assert booking.starts_at == datetime(2026, 10, 31, 15, 0, tzinfo=UTC)


async def test_t052_the_longest_appointment_fills_a_whole_opening(shop: Shop) -> None:
    result = await book_slot(
        InMemoryBookings(), shop.seller, CONVERSATION, slot(at(31, 9), minutes=240), now=NOW
    )

    assert result.status == "booked"


async def test_t052_other_sellers_bookings_do_not_count(shop: Shop) -> None:
    store = InMemoryBookings()
    other = shop.seller.model_copy(update={"seller_id": uuid4()})
    await book_slot(store, other, uuid4(), slot(at(31, 10)), now=NOW)

    result = await book_slot(store, shop.seller, CONVERSATION, slot(at(31, 10)), now=NOW)

    assert result.status == "booked"


async def test_t052_two_chats_racing_for_one_slot_get_it_once(shop: Shop) -> None:
    store = InMemoryBookings()

    results = await asyncio.gather(
        *(book_slot(store, shop.seller, uuid4(), slot(at(31, 10)), now=NOW) for _ in range(2))
    )

    assert sorted(result.status for result in results) == ["booked", "slot_taken"]


async def test_t052_without_a_free_slot_in_the_window_there_are_no_alternatives(
    shop: Shop,
) -> None:
    closed = shop.seller.model_copy(update={"business_hours": {}})

    result = await book_slot(InMemoryBookings(), closed, CONVERSATION, slot(at(31, 10)), now=NOW)

    assert (result.status, result.alternatives) == ("outside_hours", ())


async def test_t052_a_slot_past_midnight_is_outside_the_hours(shop: Shop) -> None:
    late = shop.seller.model_copy(update={"business_hours": {"sat": [["18:00", "24:00"]]}})

    result = await book_slot(InMemoryBookings(), late, CONVERSATION, slot(at(31, 23, 45)), now=NOW)

    assert result.status == "outside_hours"
    assert at(31, 23, 30) not in result.alternatives


async def test_t052_an_alternative_may_end_right_at_closing(shop: Shop) -> None:
    store = InMemoryBookings()
    await book_slot(store, shop.seller, uuid4(), slot(at(31, 12)), now=NOW)

    result = await book_slot(store, shop.seller, CONVERSATION, slot(at(31, 12, 15)), now=NOW)

    assert result.alternatives == (at(31, 12, 30), at(2, 9, month=11), at(2, 9, 30, month=11))


async def test_t052_alternatives_come_in_time_order_whatever_the_hours_order(shop: Shop) -> None:
    split = shop.seller.model_copy(
        update={"business_hours": {"sat": [["15:00", "16:00"], ["09:00", "10:00"]]}}
    )

    result = await book_slot(InMemoryBookings(), split, CONVERSATION, slot(at(31, 8)), now=NOW)

    assert result.alternatives == (at(31, 9), at(31, 9, 30), at(31, 15))


async def test_t052_a_cancelled_booking_frees_its_slot(shop: Shop) -> None:
    store = InMemoryBookings()
    store.bookings.append(
        Booking(
            uuid4(), shop.seller.seller_id, uuid4(), "Corte", at(31, 10), 30, status="cancelled"
        )
    )

    result = await book_slot(store, shop.seller, CONVERSATION, slot(at(31, 10)), now=NOW)

    assert result.status == "booked"
