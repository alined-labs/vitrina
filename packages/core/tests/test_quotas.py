"""Daily limits on paid work: the window, and a counter that never passes its limit."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from vitrina_core.quotas import InMemoryUsageCounter, day_start, seconds_to_next_day
from vitrina_core.settings import REPO_ROOT, Settings

DAY = datetime(2026, 11, 1, tzinfo=UTC)


def test_a_day_is_a_whole_utc_day_and_retry_points_at_its_end() -> None:
    bogota = timezone(timedelta(hours=-5))
    evening = datetime(2026, 11, 1, 20, 30, tzinfo=bogota)  # already Nov 2 in UTC

    assert day_start(evening) == datetime(2026, 11, 2, tzinfo=UTC)
    assert seconds_to_next_day(datetime(2026, 11, 1, 23, 59, 30, tzinfo=UTC)) == 30
    assert seconds_to_next_day(datetime(2026, 11, 1, 23, 59, 59, 999999, tzinfo=UTC)) == 1


async def test_a_counter_stops_at_its_limit_per_key_and_per_day() -> None:
    counter = InMemoryUsageCounter()

    assert [await counter.take("a", DAY, limit=2) for _ in range(3)] == [True, True, False]
    assert await counter.take("b", DAY, limit=2) is True
    assert await counter.take("a", DAY + timedelta(days=1), limit=2) is True


async def test_a_limit_of_zero_refuses_everything() -> None:
    assert await InMemoryUsageCounter().take("a", DAY, limit=0) is False


async def test_several_at_once_are_taken_whole_or_not_at_all() -> None:
    counter = InMemoryUsageCounter()

    assert await counter.take("a", DAY, limit=3, amount=2) is True
    assert await counter.take("a", DAY, limit=3, amount=2) is False  # nothing partial
    assert await counter.take("a", DAY, limit=3) is True
    assert await InMemoryUsageCounter().take("a", DAY, limit=1, amount=2) is False


async def test_what_was_given_back_can_be_taken_again_and_never_goes_below_zero() -> None:
    counter = InMemoryUsageCounter()
    await counter.take("a", DAY, limit=1)

    await counter.give_back("a", DAY)
    assert await counter.take("a", DAY, limit=1) is True
    await counter.give_back("a", DAY, amount=5)
    await counter.give_back("never-used", DAY)
    assert [await counter.take("a", DAY, limit=2) for _ in range(3)] == [True, True, False]


@pytest.mark.parametrize(
    "name",
    [
        "public_showcase_daily_cap",
        "seller_daily_agent_turns",
        "seller_daily_creative_jobs",
        "seller_daily_paid_jobs",
    ],
)
def test_a_daily_limit_cannot_be_negative(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings(_env_file=None, **{name: -1})  # type: ignore[call-arg]


def test_a_text_limit_must_leave_room_for_a_message() -> None:
    with pytest.raises(ValueError, match="agent_turn_max_chars"):
        Settings(_env_file=None, agent_turn_max_chars=0)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "name",
    [
        "public_showcase_daily_cap",
        "seller_daily_agent_turns",
        "seller_daily_creative_jobs",
        "seller_daily_paid_jobs",
        "agent_turn_max_chars",
    ],
)
def test_env_example_documents_each_limit_with_its_default(name: str) -> None:
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    default = getattr(Settings(_env_file=None), name)  # type: ignore[call-arg]

    assert f"{name.upper()}={default}" in example
