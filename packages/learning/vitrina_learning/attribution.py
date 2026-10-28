"""Attribution analytics (T044; AS-4.1, FR-040, FR-041).

The conversation module emits ``ad.*`` events as chats open, reserve, book, hand off and
object (data-model.md §4). ``AttributionConsumer`` copies them into ``attribution_events``,
idempotent on the event id, and the reports aggregate that table per ad and per angle.
Objection tags come from the ``ad.objection`` and ``objection.recorded`` events themselves:
learning reads conversation data through events only (plan.md §3). Periods are whole UTC
days with ``to`` included, as for ``/metrics/costs`` (FR-085).
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.events import EventType, StoredEvent, events_after, tally
from vitrina_core.ledger import period_bounds
from vitrina_creative.api import AdStats, AdStore
from vitrina_learning.schemas import (
    AdAttribution,
    AdObjections,
    AngleAttribution,
    AttributionReport,
    ObjectionReport,
)
from vitrina_learning.tables import attribution_events

logger = logging.getLogger("vitrina.learning")

KINDS: dict[EventType, str] = {
    "ad.chat_started": "chat_started",
    "ad.reservation": "reservation",
    "ad.booking": "booking",
    "ad.handoff": "handoff",
    "ad.objection": "objection",
}
_KIND_OF = {str(event_type): kind for event_type, kind in KINDS.items()}
# A copied row's id derives from its event's id, so reading an event again changes nothing.
ROW_NAMESPACE = UUID("0192f2a0-5b7e-7c3d-9a41-6e2f8b0c1d55")


@dataclass(frozen=True)
class AdInfo:
    code: str | None
    angle: str | None
    status: str


class AdDirectory(Protocol):
    async def ads(self, seller_id: UUID) -> Mapping[UUID, AdInfo]: ...


class CreativeAds:
    """``AdDirectory`` over ``creative.api.AdStore``: every ad of the seller, page by page."""

    def __init__(self, store: AdStore) -> None:
        self.store = store

    async def ads(self, seller_id: UUID) -> dict[UUID, AdInfo]:
        found: dict[UUID, AdInfo] = {}
        cursor: str | None = None
        while True:
            page, cursor = await self.store.page(seller_id, limit=100, cursor=cursor)
            for ad in page:
                angle = None if ad.angle is MISSING else str(ad.angle)
                found[ad.id] = AdInfo(code=ad.code, angle=angle, status=ad.status)
            if cursor is None:
                return found


class AttributionConsumer:
    """Copies ``ad.*`` events into ``attribution_events``: the worker's sweep.

    The cursor lives in the process. After a restart the consumer reads the events again from
    the start and skips the rows it already copied.
    """

    def __init__(self, *, batch: int = 500) -> None:
        self.after_id = 0
        self.batch = batch

    async def sweep(self, sessions: async_sessionmaker[AsyncSession], now: datetime) -> int:
        copied = 0
        while True:
            async with sessions() as session, session.begin():
                found = await events_after(session, self.after_id, types=KINDS, limit=self.batch)
                for event in found:
                    copied += await _copy(session, event)
            if found:
                self.after_id = found[-1].id
            if len(found) < self.batch:
                return copied


async def _copy(session: AsyncSession, event: StoredEvent) -> int:
    row = _row(event)
    if row is None:
        return 0
    try:
        async with session.begin_nested():
            inserted = await session.execute(
                insert(attribution_events)
                .values(**row)
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(attribution_events.c.id)
            )
            return len(inserted.all())
    except IntegrityError:  # the seller, ad or conversation is gone: nothing to attribute
        logger.warning("attribution: event %d refers to a deleted row; skipped", event.id)
        return 0


def _row(event: StoredEvent) -> dict[str, Any] | None:
    if event.seller_id is None:
        return None
    payload = event.payload
    occurred = payload.get("occurred_at")
    try:
        occurred_at = datetime.fromisoformat(occurred) if occurred else event.created_at
    except TypeError, ValueError:
        occurred_at = event.created_at
    if occurred_at.tzinfo is None:  # FR-085: timestamps are UTC
        occurred_at = occurred_at.replace(tzinfo=UTC)
    return {
        "id": uuid.uuid5(ROW_NAMESPACE, str(event.id)),
        "seller_id": event.seller_id,
        "ad_id": _uuid(payload.get("ad_id")),
        "conversation_id": _uuid(payload.get("conversation_id")),
        "kind": _KIND_OF[event.type],
        "occurred_at": occurred_at,
    }


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _rate(conversions: int, chats: int) -> float:
    return round(conversions / chats, 4) if chats else 0.0


def build_attribution_report(
    period_from: date,
    period_to: date,
    *,
    counts: Mapping[tuple[UUID, str], int],
    tags: Mapping[tuple[UUID, str], int],
    ads: Mapping[UUID, AdInfo],
) -> AttributionReport:
    """Every approved ad, plus any other ad with activity in the period, by code."""
    active = {ad_id for ad_id, _ in counts} | {ad_id for ad_id, _ in tags}
    rows: list[AdAttribution] = []
    angles: dict[str, list[int]] = {}
    for ad_id, info in sorted(ads.items(), key=lambda item: item[1].code or ""):
        if info.code is None or (info.status != "approved" and ad_id not in active):
            continue
        chats = counts.get((ad_id, "chat_started"), 0)
        reservations = counts.get((ad_id, "reservation"), 0)
        bookings = counts.get((ad_id, "booking"), 0)
        by_tag = {tag: n for (tagged, tag), n in tags.items() if tagged == ad_id}
        rows.append(
            AdAttribution.model_validate(
                {
                    "ad_id": ad_id,
                    "code": info.code,
                    "chats": chats,
                    "reservations": reservations,
                    "bookings": bookings,
                    "handoffs": counts.get((ad_id, "handoff"), 0),
                    "conversion_rate": _rate(reservations + bookings, chats),
                    **({"angle": info.angle} if info.angle else {}),
                    **({"objections_by_tag": by_tag} if by_tag else {}),
                }
            )
        )
        if info.angle:
            total = angles.setdefault(info.angle, [0, 0])
            total[0] += chats
            total[1] += reservations + bookings
    return AttributionReport(
        period_from=period_from,
        period_to=period_to,
        ads=rows,
        by_angle={
            angle: AngleAttribution(chats=chats, conversion_rate=_rate(converted, chats))
            for angle, (chats, converted) in angles.items()
        },
    )


def build_objection_report(
    period_from: date,
    period_to: date,
    *,
    totals: Mapping[str, int],
    by_ad: Mapping[tuple[UUID, str], int],
    ads: Mapping[UUID, AdInfo],
) -> ObjectionReport:
    tags: dict[UUID, dict[str, int]] = defaultdict(dict)
    for (ad_id, tag), count in by_ad.items():
        tags[ad_id][tag] = count
    known = [(ad_id, ads[ad_id]) for ad_id in tags if ad_id in ads and ads[ad_id].code]
    return ObjectionReport(
        period_from=period_from,
        period_to=period_to,
        totals=dict(totals),
        by_ad=[
            AdObjections(ad_id=ad_id, code=info.code or "", tags=tags[ad_id])
            for ad_id, info in sorted(known, key=lambda item: item[1].code or "")
        ],
    )


async def attribution_report(
    sessions: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    period_from: date,
    period_to: date,
    *,
    ads: AdDirectory,
) -> AttributionReport:
    start, end = period_bounds(period_from, period_to)
    rows = attribution_events.c
    async with sessions() as session:
        found = await session.execute(
            sa.select(rows.ad_id, rows.kind, sa.func.count())
            .where(
                rows.seller_id == seller_id,
                rows.ad_id.is_not(None),
                rows.occurred_at >= start,
                rows.occurred_at < end,
            )
            .group_by(rows.ad_id, rows.kind)
        )
        counts = {(ad_id, kind): int(n) for ad_id, kind, n in found}
        objections = await tally(
            session, seller_id, "ad.objection", start=start, end=end, by=("ad_id", "tag")
        )
    return build_attribution_report(
        period_from,
        period_to,
        counts=counts,
        tags=_by_ad(objections),
        ads=await ads.ads(seller_id),
    )


async def attributed_chats(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID, period_from: date, period_to: date
) -> int:
    """Chats that came from an ad in the period: AS-5.1's threshold for a brief."""
    start, end = period_bounds(period_from, period_to)
    rows = attribution_events.c
    async with sessions() as session:
        counted = await session.scalar(
            sa.select(sa.func.count()).where(
                rows.seller_id == seller_id,
                rows.kind == "chat_started",
                rows.ad_id.is_not(None),
                rows.occurred_at >= start,
                rows.occurred_at < end,
            )
        )
    return int(counted or 0)


async def objection_report(
    sessions: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    period_from: date,
    period_to: date,
    *,
    ads: AdDirectory,
) -> ObjectionReport:
    start, end = period_bounds(period_from, period_to)
    async with sessions() as session:
        recorded = await tally(
            session, seller_id, "objection.recorded", start=start, end=end, by=("tag",)
        )
        per_ad = await tally(
            session, seller_id, "ad.objection", start=start, end=end, by=("ad_id", "tag")
        )
    return build_objection_report(
        period_from,
        period_to,
        totals={tag: n for (tag,), n in recorded.items() if tag},
        by_ad=_by_ad(per_ad),
        ads=await ads.ads(seller_id),
    )


def _by_ad(tallied: Mapping[tuple[str | None, ...], int]) -> dict[tuple[UUID, str], int]:
    counts: dict[tuple[UUID, str], int] = {}
    for (ad_text, tag), n in tallied.items():
        ad_id = _uuid(ad_text)
        if ad_id is not None and tag:
            counts[(ad_id, tag)] = n
    return counts


async def ad_stats(
    sessions: async_sessionmaker[AsyncSession], seller_id: UUID, ad_ids: Iterable[UUID]
) -> dict[UUID, AdStats]:
    """``Ad.stats`` for the ads routes: lifetime chats, reservations and bookings per ad, for
    the ads that had any (seller-scoped, Art. V.3)."""
    wanted = list(ad_ids)
    if not wanted:
        return {}
    rows = attribution_events.c
    async with sessions() as session:
        found = await session.execute(
            sa.select(rows.ad_id, rows.kind, sa.func.count())
            .where(
                rows.seller_id == seller_id,
                rows.ad_id.in_(wanted),
                rows.kind.in_(("chat_started", "reservation", "booking")),
            )
            .group_by(rows.ad_id, rows.kind)
        )
    counts: dict[UUID, dict[str, int]] = defaultdict(dict)
    for ad_id, kind, n in found:
        counts[ad_id][kind] = int(n)
    return {
        ad_id: AdStats(
            chats=kinds.get("chat_started", 0),
            reservations=kinds.get("reservation", 0),
            bookings=kinds.get("booking", 0),
        )
        for ad_id, kinds in counts.items()
    }
