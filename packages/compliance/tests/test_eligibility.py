from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from vitrina_compliance.calendar import BOGOTA, is_holiday, is_legal, next_legal
from vitrina_compliance.eligibility import ContactState, evaluate
from vitrina_core.settings import REPO_ROOT

CASES: list[dict[str, Any]] = yaml.safe_load(
    (REPO_ROOT / "evals" / "fixtures" / "consent_cases.yaml").read_text(encoding="utf-8")
)
CONTACT = UUID("00000000-0000-7000-8000-0000000000c1")
AD = UUID("00000000-0000-7000-8000-0000000000d1")


def contact(**facts: Any) -> ContactState:
    last = facts.get("last_marketing_sent_at")
    return ContactState(
        contact_id=CONTACT,
        opted_out=facts.get("opted_out", False),
        marketing_consent=facts.get("consent", True),
        last_marketing_sent_at=datetime.fromisoformat(last) if last else None,
        matching_ad_id=AD if facts.get("matching_ad", True) else None,
    )


def test_the_table_has_forty_distinct_cases() -> None:
    assert len(CASES) == 40
    assert len({case["id"] for case in CASES}) == 40


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_as_6_2_to_6_4_eligibility_follows_the_table(case: dict[str, Any]) -> None:
    at = datetime.fromisoformat(case["at"])

    decision = evaluate(contact(**case.get("contact", {})), at)

    expected = case["expect"]
    assert (decision.decision, decision.reason) == (expected["decision"], expected["reason"])
    if "next_allowed_at" in expected:
        assert decision.next_allowed_at == datetime.fromisoformat(expected["next_allowed_at"])
    else:
        assert decision.next_allowed_at is None
    assert decision.contact_id == CONTACT
    assert decision.evaluated_at == at
    assert decision.reason_text_es


def test_fr_061_the_reason_texts_are_spanish_and_name_the_law() -> None:
    at = datetime(2026, 10, 28, 9, tzinfo=BOGOTA)

    assert evaluate(contact(consent=False), at).reason_text_es == "Sin autorización — Ley 1581"
    deferred = evaluate(contact(), datetime(2026, 11, 1, 10, tzinfo=BOGOTA))
    assert "Ley 2300" in deferred.reason_text_es


def test_fr_062_q08_moved_holidays_are_holidays_in_the_pinned_calendar() -> None:
    assert is_holiday(date(2026, 11, 2))  # Todos los Santos, moved to Monday
    assert is_holiday(date(2026, 11, 16))  # Independencia de Cartagena, moved to Monday
    assert is_holiday(date(2027, 1, 11))  # Reyes Magos 2027, moved to Monday
    assert not is_holiday(date(2026, 11, 3))
    assert not is_holiday(date(2026, 11, 1))  # a Sunday, not the holiday itself


def test_a_naive_time_is_refused_instead_of_guessed() -> None:
    naive = datetime(2026, 10, 28, 9)  # noqa: DTZ001 - the point of the test

    with pytest.raises(ValueError, match="time zone"):
        is_legal(naive)
    with pytest.raises(ValueError, match="time zone"):
        evaluate(contact(), naive)


def test_next_legal_is_the_time_itself_inside_a_window() -> None:
    at = datetime(2026, 10, 28, 9, 30, tzinfo=BOGOTA)

    assert next_legal(at) == at


def test_every_next_legal_time_is_legal_for_a_year_of_hours() -> None:
    at = datetime(2026, 10, 1, 0, 0, tzinfo=BOGOTA)
    while at < datetime(2027, 10, 1, tzinfo=BOGOTA):
        allowed = next_legal(at)
        assert is_legal(allowed), at
        assert allowed >= at
        assert allowed - at < timedelta(days=5), at
        at += timedelta(hours=1)


def test_the_table_file_is_where_the_readme_says() -> None:
    assert Path(REPO_ROOT / "evals" / "fixtures" / "consent_cases.yaml").is_file()
