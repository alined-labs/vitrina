"""T044 end to end on Postgres: ``ad.*`` events become attribution rows and reports."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.events import emit
from vitrina_core.ids import new_id
from vitrina_creative.api import AdStats, AdStore
from vitrina_learning.api import (
    AttributionConsumer,
    CreativeAds,
    ad_stats,
    attribution_report,
    objection_report,
)
from vitrina_learning.testing import approved_ad, conversation

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")


@pytest.mark.db
async def test_db_as_4_1_ad_events_become_the_funnel_tags_and_ad_stats(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessions() as session, session.begin():
        ad = await approved_ad(session, SELLER, "+573000000001")
        other_ad = await approved_ad(session, OTHER, "+573000000002")
        first, second = await conversation(session, SELLER), await conversation(session, SELLER)
        stranger = await conversation(session, OTHER)
        on = {"ad_id": str(ad)}
        for chat in (first, second):
            payload = on | {
                "conversation_id": str(chat),
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            await emit(session, "ad.chat_started", payload, seller_id=SELLER)
        await emit(
            session,
            "ad.reservation",
            on | {"conversation_id": str(first), "reservation_id": str(new_id())},
            seller_id=SELLER,
        )
        await emit(
            session,
            "ad.booking",
            on | {"conversation_id": str(second), "booking_id": str(new_id())},
            seller_id=SELLER,
        )
        await emit(
            session,
            "ad.handoff",
            on | {"conversation_id": str(first), "handoff_id": str(new_id()), "reason": "other"},
            seller_id=SELLER,
        )
        await emit(
            session,
            "ad.objection",
            on | {"conversation_id": str(first), "objection_id": str(new_id()), "tag": "shipping"},
            seller_id=SELLER,
        )
        for tag in ("shipping", "price"):
            await emit(
                session,
                "objection.recorded",
                {"objection_id": str(new_id()), "tag": tag, "product_id": None},
                seller_id=SELLER,
            )
        await emit(
            session,
            "ad.chat_started",
            {"ad_id": str(other_ad), "conversation_id": str(stranger)},
            seller_id=OTHER,
        )

    now = datetime.now(UTC)
    assert await AttributionConsumer().sweep(db_sessions, now) == 7
    assert await AttributionConsumer().sweep(db_sessions, now) == 0  # idempotent on event ids

    start, end = (now - timedelta(days=1)).date(), (now + timedelta(days=1)).date()
    ads = CreativeAds(AdStore(db_sessions))
    report = await attribution_report(db_sessions, SELLER, start, end, ads=ads)
    [row] = report.ads
    assert (row.ad_id, row.code, row.angle) == (ad, "A7", "price")
    assert (row.chats, row.reservations, row.bookings, row.handoffs) == (2, 1, 1, 1)
    assert row.conversion_rate == 1.0
    assert row.objections_by_tag == {"shipping": 1}
    assert report.model_dump(mode="json")["by_angle"] == {
        "price": {"chats": 2, "conversion_rate": 1.0}
    }

    objections = await objection_report(db_sessions, SELLER, start, end, ads=ads)
    assert objections.totals == {"shipping": 1, "price": 1}
    assert [(item.ad_id, item.tags) for item in objections.by_ad] == [(ad, {"shipping": 1})]

    assert await ad_stats(db_sessions, SELLER, [ad, other_ad]) == {
        ad: AdStats(chats=2, reservations=1, bookings=1)
    }
