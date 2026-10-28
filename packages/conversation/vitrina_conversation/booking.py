"""Appointments for ``book_slot`` (T052; contracts/agent-tools.json; data-model.md bookings).

A slot is booked when it fits inside one of the seller's opening intervals that day, in the
seller's timezone, and overlaps none of the seller's other bookings. The settings carry no
capacity, so a seller takes one appointment at a time (Q37, assumed). A
refused or past slot comes back as ``outside_hours`` or ``slot_taken`` with up to three free
slots of the same length, on a 30-minute grid from each opening, within the next seven days
(Q37, assumed). A time without an offset is the seller's local time. Each seller's bookings
are checked and written under one lock (a Postgres advisory lock), so two chats cannot take
the same slot.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import SellerSettings
from vitrina_catalog.sellers import BusinessHours
from vitrina_conversation import tables as db
from vitrina_core.ids import new_id

BookingStatus = Literal["booked", "outside_hours", "slot_taken"]
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SLOT_STEP = timedelta(minutes=30)  # Q37, assumed
SEARCH_WINDOW = timedelta(days=7)  # Q37, assumed
MAX_ALTERNATIVES = 3  # the contract's maxItems
LONGEST = timedelta(minutes=240)  # duration_min's upper bound (contract, bookings check)

Span = tuple[datetime, datetime]


@dataclass(frozen=True)
class SlotRequest:
    service: str
    starts_at: datetime
    duration_min: int
    customer_name: str | None = None


@dataclass(frozen=True)
class Booking:
    id: UUID
    seller_id: UUID
    conversation_id: UUID | None
    service: str
    starts_at: datetime
    duration_min: int
    customer_name: str | None = None
    status: str = "booked"

    @property
    def span(self) -> Span:
        return self.starts_at, self.starts_at + timedelta(minutes=self.duration_min)


@dataclass(frozen=True)
class BookingResult:
    status: BookingStatus
    booking_id: UUID | None = None
    alternatives: tuple[datetime, ...] = ()
    starts_at: datetime | None = None
    """When booked: the start in the seller's local time."""


class SellerBookings(Protocol):
    """One seller's bookings, under that seller's lock."""

    async def taken(self, start: datetime, end: datetime) -> list[Span]: ...

    async def add(self, conversation_id: UUID, slot: SlotRequest) -> UUID: ...


class BookingStore(Protocol):
    def locked(self, seller_id: UUID) -> AbstractAsyncContextManager[SellerBookings]: ...


async def book_slot(
    store: BookingStore,
    seller: SellerSettings,
    conversation_id: UUID,
    slot: SlotRequest,
    *,
    now: datetime,
) -> BookingResult:
    zone = ZoneInfo(seller.timezone)
    start = slot.starts_at if slot.starts_at.tzinfo else slot.starts_at.replace(tzinfo=zone)
    request = SlotRequest(slot.service, start, slot.duration_min, slot.customer_name)
    length = timedelta(minutes=slot.duration_min)
    after = max(start, now)
    async with store.locked(seller.seller_id) as bookings:
        taken = await bookings.taken(now - length, after + SEARCH_WINDOW + length)
        if start < now or not fits(seller.business_hours, zone, start, length):
            status: BookingStatus = "outside_hours"
        elif any(overlaps((start, start + length), other) for other in taken):
            status = "slot_taken"
        else:
            booking_id = await bookings.add(conversation_id, request)
            return BookingResult("booked", booking_id, starts_at=start.astimezone(zone))
    free = free_slots(seller.business_hours, zone, after, length, taken)
    return BookingResult(status, alternatives=tuple(free))


def fits(hours: BusinessHours, zone: ZoneInfo, start: datetime, length: timedelta) -> bool:
    """Inside one opening interval of that local day; nothing runs past midnight."""
    local = start.astimezone(zone)
    begin = local.hour * 60 + local.minute
    end = begin + length.total_seconds() / 60
    return any(
        _minutes(opens) <= begin and end <= _minutes(closes)
        for opens, closes in hours.get(WEEKDAY_KEYS[local.weekday()], [])
    )


def overlaps(one: Span, other: Span) -> bool:
    return one[0] < other[1] and other[0] < one[1]


def free_slots(
    hours: BusinessHours,
    zone: ZoneInfo,
    after: datetime,
    length: timedelta,
    taken: Sequence[Span],
) -> list[datetime]:
    """Up to three free starts from ``after``, on the grid of each opening, in local time."""
    found: list[datetime] = []
    day = after.astimezone(zone).date()
    last = (after + SEARCH_WINDOW).astimezone(zone).date()
    while day <= last and len(found) < MAX_ALTERNATIVES:
        midnight = datetime(day.year, day.month, day.day, tzinfo=zone)
        for opens, closes in sorted(hours.get(WEEKDAY_KEYS[day.weekday()], [])):
            candidate = midnight + timedelta(minutes=_minutes(opens))
            closing = midnight + timedelta(minutes=_minutes(closes))
            while candidate + length <= closing and len(found) < MAX_ALTERNATIVES:
                span = (candidate, candidate + length)
                if candidate >= after and not any(overlaps(span, other) for other in taken):
                    found.append(candidate)
                candidate += SLOT_STEP
        day += timedelta(days=1)
    return found


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


# ── stores ──


@dataclass
class InMemoryBookings:
    bookings: list[Booking] = field(default_factory=list)
    _locks: dict[UUID, asyncio.Lock] = field(default_factory=dict)

    @asynccontextmanager
    async def locked(self, seller_id: UUID) -> AsyncIterator[SellerBookings]:
        async with self._locks.setdefault(seller_id, asyncio.Lock()):
            yield _MemorySellerBookings(self, seller_id)


@dataclass(frozen=True)
class _MemorySellerBookings:
    store: InMemoryBookings
    seller_id: UUID

    async def taken(self, start: datetime, end: datetime) -> list[Span]:
        found = [
            booking.span
            for booking in self.store.bookings
            if booking.seller_id == self.seller_id
            and booking.status == "booked"
            and overlaps(booking.span, (start, end))
        ]
        await asyncio.sleep(0)  # a snapshot, then a racing chat may run, as with a database
        return found

    async def add(self, conversation_id: UUID, slot: SlotRequest) -> UUID:
        booking = Booking(
            new_id(),
            self.seller_id,
            conversation_id,
            slot.service,
            slot.starts_at.astimezone(UTC),
            slot.duration_min,
            slot.customer_name,
        )
        self.store.bookings.append(booking)
        return booking.id


class PostgresBookings:
    """Bookings on Postgres; a transaction-scoped advisory lock per seller serializes them."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @asynccontextmanager
    async def locked(self, seller_id: UUID) -> AsyncIterator[SellerBookings]:
        async with self.sessions() as session, session.begin():
            key = int.from_bytes(seller_id.bytes[:8], "big", signed=True)
            await session.execute(
                sa.select(sa.func.pg_advisory_xact_lock(sa.cast(key, sa.BigInteger)))
            )
            yield _PostgresSellerBookings(session, seller_id)


@dataclass(frozen=True)
class _PostgresSellerBookings:
    session: AsyncSession
    seller_id: UUID

    async def taken(self, start: datetime, end: datetime) -> list[Span]:
        """Rows that start early enough to reach ``start``; the overlap is checked here."""
        bookings = db.bookings.c
        rows = await self.session.execute(
            sa.select(bookings.starts_at, bookings.duration_min).where(
                bookings.seller_id == self.seller_id,
                bookings.status == "booked",
                bookings.starts_at < end,
                bookings.starts_at > start - LONGEST,
            )
        )
        spans = [
            (row.starts_at, row.starts_at + timedelta(minutes=row.duration_min)) for row in rows
        ]
        return [span for span in spans if overlaps(span, (start, end))]

    async def add(self, conversation_id: UUID, slot: SlotRequest) -> UUID:
        booking_id = new_id()
        await self.session.execute(
            sa.insert(db.bookings).values(
                id=booking_id,
                seller_id=self.seller_id,
                conversation_id=conversation_id,
                service=slot.service,
                starts_at=slot.starts_at,
                duration_min=slot.duration_min,
                customer_name=slot.customer_name,
            )
        )
        return booking_id
