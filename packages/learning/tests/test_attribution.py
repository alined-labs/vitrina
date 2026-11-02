from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from vitrina_core.events import StoredEvent
from vitrina_learning import attribution
from vitrina_learning.attribution import (
    AdInfo,
    AttributionConsumer,
    build_attribution_report,
    build_objection_report,
)

PRICE = UUID("00000000-0000-7000-8000-0000000000a1")
GIFT = UUID("00000000-0000-7000-8000-0000000000a2")
QUIET = UUID("00000000-0000-7000-8000-0000000000a3")
OLD = UUID("00000000-0000-7000-8000-0000000000a4")
ADS = {
    PRICE: AdInfo(code="A7", angle="price", status="approved"),
    GIFT: AdInfo(code="A8", angle="gift", status="approved"),
    QUIET: AdInfo(code="A9", angle="price", status="approved"),
    OLD: AdInfo(code="AB", angle=None, status="archived"),
}
PERIOD = (date(2026, 10, 1), date(2026, 10, 28))


def test_as_4_1_each_ad_reports_its_funnel_and_conversion() -> None:
    counts = {
        (PRICE, "chat_started"): 4,
        (PRICE, "reservation"): 1,
        (PRICE, "booking"): 1,
        (PRICE, "handoff"): 2,
        (GIFT, "chat_started"): 2,
    }
    tags = {(PRICE, "shipping"): 2, (PRICE, "price"): 1}

    report = build_attribution_report(*PERIOD, counts=counts, tags=tags, ads=ADS)

    rows = {row.ad_id: row.model_dump() for row in report.ads}
    assert rows[PRICE] == {
        "ad_id": PRICE,
        "code": "A7",
        "angle": "price",
        "chats": 4,
        "reservations": 1,
        "bookings": 1,
        "handoffs": 2,
        "conversion_rate": 0.5,
        "objections_by_tag": {"shipping": 2, "price": 1},
    }
    assert rows[GIFT]["conversion_rate"] == 0.0
    assert "objections_by_tag" not in rows[GIFT]


def test_as_4_1_approved_ads_without_chats_still_show_and_old_ads_only_with_activity() -> None:
    report = build_attribution_report(
        *PERIOD, counts={(PRICE, "chat_started"): 1}, tags={}, ads=ADS
    )

    assert [row.code for row in report.ads] == ["A7", "A8", "A9"]
    assert report.ads[2].chats == 0
    assert report.ads[2].conversion_rate == 0.0


def test_as_4_1_chats_no_ad_started_are_counted_apart() -> None:
    report = build_attribution_report(
        *PERIOD, counts={(PRICE, "chat_started"): 1}, tags={}, ads=ADS, unattributed=3
    )

    assert report.unattributed_chats == 3
    assert sum(row.chats for row in report.ads) == 1


def test_fr_041_angles_add_up_their_ads() -> None:
    counts = {
        (PRICE, "chat_started"): 3,
        (PRICE, "reservation"): 1,
        (QUIET, "chat_started"): 1,
        (QUIET, "booking"): 1,
        (OLD, "chat_started"): 5,
    }

    report = build_attribution_report(*PERIOD, counts=counts, tags={}, ads=ADS)

    assert report.model_dump(mode="json")["by_angle"] == {
        "price": {"chats": 4, "conversion_rate": 0.5},
        "gift": {"chats": 0, "conversion_rate": 0.0},
    }
    assert {row.code for row in report.ads} == {"A7", "A8", "A9", "AB"}


def test_fr_041_objections_report_totals_and_tags_per_ad() -> None:
    report = build_objection_report(
        *PERIOD,
        totals={"price": 3, "shipping": 2},
        by_ad={(PRICE, "shipping"): 2, (PRICE, "price"): 1, (GIFT, "price"): 1},
        ads=ADS,
    )

    assert report.model_dump(mode="json") == {
        "period_from": "2026-10-01",
        "period_to": "2026-10-28",
        "totals": {"price": 3, "shipping": 2},
        "by_ad": [
            {"ad_id": str(PRICE), "code": "A7", "tags": {"shipping": 2, "price": 1}},
            {"ad_id": str(GIFT), "code": "A8", "tags": {"price": 1}},
        ],
    }


# ── the cursor: ids are handed out at insert, not at commit ──

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


class FakeSessions:
    """Enough of a session factory for ``async with sessions() as s, s.begin()``."""

    def __call__(self) -> FakeSessions:
        return self

    def begin(self) -> FakeSessions:
        return self

    async def __aenter__(self) -> FakeSessions:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def stored(event_id: int, *, age_s: float) -> StoredEvent:
    created_at = NOW - timedelta(seconds=age_s)
    return StoredEvent(event_id, None, "ad.chat_started", {}, created_at)


@pytest.fixture
def visible(monkeypatch: pytest.MonkeyPatch) -> tuple[list[StoredEvent], list[int]]:
    """The committed events, and the ids copied from them."""
    events: list[StoredEvent] = []
    copied: list[int] = []

    async def events_after(
        _session: Any, after_id: int, *, types: Collection[str], limit: int
    ) -> list[StoredEvent]:
        return sorted(
            (event for event in events if event.id > after_id), key=lambda event: event.id
        )[:limit]

    async def copy(_session: Any, event: StoredEvent) -> int:
        if event.id in copied:
            return 0
        copied.append(event.id)
        return 1

    monkeypatch.setattr(attribution, "events_after", events_after)
    monkeypatch.setattr(attribution, "_copy", copy)
    return events, copied


async def test_as_4_1_an_event_that_commits_after_a_later_id_is_still_copied(
    visible: tuple[list[StoredEvent], list[int]],
) -> None:
    events, copied = visible
    consumer = AttributionConsumer(commit_grace_s=300)
    sessions: Any = FakeSessions()

    events.append(stored(6, age_s=1))  # event 5's transaction is still open
    assert await consumer.sweep(sessions, NOW) == 1
    events.append(stored(5, age_s=2))  # now it commits
    assert await consumer.sweep(sessions, NOW) == 1

    assert sorted(copied) == [5, 6]


async def test_as_4_1_the_cursor_passes_settled_events_only_and_stops_at_the_first_young_one(
    visible: tuple[list[StoredEvent], list[int]],
) -> None:
    events, _ = visible
    consumer = AttributionConsumer(commit_grace_s=300)
    events += [stored(1, age_s=900), stored(2, age_s=900), stored(3, age_s=1), stored(4, age_s=900)]

    assert await consumer.sweep(FakeSessions(), NOW) == 4  # type: ignore[arg-type]

    assert consumer.after_id == 2


async def test_as_4_1_a_full_batch_of_young_events_does_not_hold_the_sweep_on_one_page(
    visible: tuple[list[StoredEvent], list[int]],
) -> None:
    events, copied = visible
    consumer = AttributionConsumer(batch=2, commit_grace_s=300)
    events += [stored(event_id, age_s=1) for event_id in range(1, 6)]

    assert await consumer.sweep(FakeSessions(), NOW) == 5  # type: ignore[arg-type]

    assert copied == [1, 2, 3, 4, 5]
    assert consumer.after_id == 0
