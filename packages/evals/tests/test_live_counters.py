from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from vitrina_evals.live import Counters, live_counters, load_counters

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
SESSIONS: Any = object()


async def test_fr_070_live_counters_merge_what_each_module_reports() -> None:
    async def conversations(_s: Any, _seller: UUID, _since: datetime) -> Mapping[str, float]:
        return {"conversations_today": 3, "attributed_rate": 0.5}

    async def creative(_s: Any, _seller: UUID, _since: datetime) -> Mapping[str, float]:
        return {"creatives_blocked_total": 2, "not_in_the_contract": 1}

    async def broken(_s: Any, _seller: UUID, _since: datetime) -> Mapping[str, float]:
        raise RuntimeError("boom")

    live = await live_counters([conversations, creative, broken], SESSIONS, SELLER, now=NOW)

    assert live.model_dump() == {
        "conversations_today": 3,
        "attributed_rate": 0.5,
        "creatives_blocked_total": 2,
    }


async def test_counters_count_from_the_start_of_the_day_in_bogota() -> None:
    seen: list[tuple[UUID, datetime]] = []

    async def spy(_s: Any, seller: UUID, since: datetime) -> Mapping[str, float]:
        seen.append((seller, since))
        return {}

    # 03:00 UTC on Oct 28 is still Oct 27 in Bogotá (UTC-5).
    await live_counters([spy], SESSIONS, SELLER, now=datetime(2026, 10, 28, 3, 0, tzinfo=UTC))

    assert seen == [(SELLER, datetime(2026, 10, 27, 5, 0, tzinfo=UTC))]


async def test_without_a_database_no_counter_runs() -> None:
    async def spy(_s: Any, _seller: UUID, _since: datetime) -> Mapping[str, float]:
        raise AssertionError("must not run")

    assert (await live_counters([spy], None, SELLER, now=NOW)).model_dump() == {}


def test_counters_come_from_the_vitrina_metrics_entry_points() -> None:
    async def count(_s: Any, _seller: UUID, _since: datetime) -> Mapping[str, float]:
        return {}

    class Point:
        name = "conversation"

        def load(self) -> Counters:
            return count

    class Broken:
        name = "creative"

        def load(self) -> Counters:
            raise ImportError("missing dependency")

    assert load_counters([Point(), Broken()]) == [count]
