from datetime import date
from uuid import UUID

from vitrina_learning.attribution import AdInfo, build_attribution_report, build_objection_report

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
