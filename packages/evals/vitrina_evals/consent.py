"""The consent_scheduler suite: the eligibility table, run as an eval (T065, ADR-0008).

``evals/fixtures/consent_cases.yaml`` holds 40 cases of time, consent, opt-out, frequency
and ads; each must give exactly the expected decision, reason and next legal time. The suite
passes only at 100 % (spec.md §7), so CI fails on any regression in the fixed order or the
Ley 2300 calendar.
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from vitrina_compliance.api import ContactState, evaluate
from vitrina_evals.cases import FIXTURES
from vitrina_evals.runner import SuiteResult
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import CaseScore

CONSENT_CASES = FIXTURES / "consent_cases.yaml"
CONTACT = UUID("00000000-0000-7000-8000-0000000000c1")
AD = UUID("00000000-0000-7000-8000-0000000000d1")


def load_consent_cases(path: Path = CONSENT_CASES) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cases


def contact_state(facts: dict[str, Any]) -> ContactState:
    """A case's contact: consent granted, not opted out, no marketing yet, a matching ad."""
    last = facts.get("last_marketing_sent_at")
    return ContactState(
        contact_id=CONTACT,
        opted_out=facts.get("opted_out", False),
        marketing_consent=facts.get("consent", True),
        last_marketing_sent_at=datetime.fromisoformat(last) if last else None,
        matching_ad_id=AD if facts.get("matching_ad", True) else None,
    )


def _score(case: dict[str, Any]) -> CaseScore:
    expected = case["expect"]
    decision = evaluate(contact_state(case.get("contact", {})), datetime.fromisoformat(case["at"]))
    wanted_next = expected.get("next_allowed_at")
    actual_next = decision.next_allowed_at.isoformat() if decision.next_allowed_at else None
    same_next = (
        actual_next is None
        if wanted_next is None
        else decision.next_allowed_at == datetime.fromisoformat(wanted_next)
    )
    passed = (decision.decision, decision.reason) == (expected["decision"], expected["reason"])
    passed = passed and same_next
    notes = "ok" if passed else f"expected {expected['decision']}/{expected['reason']}"
    if not passed and wanted_next is not None:
        notes += f" next {wanted_next}"
    return CaseScore(
        case_id=case["id"],
        passed=passed,
        notes=notes,
        expected=dict(expected),
        actual={
            "decision": decision.decision,
            "reason": decision.reason,
            "next_allowed_at": actual_next,
        },
    )


def run_consent_scheduler(cases: Sequence[dict[str, Any]]) -> SuiteResult:
    scores = [_score(case) for case in cases]
    accuracy = round(sum(score.passed for score in scores) / len(scores), 4) if scores else 0.0
    metrics = EvalMetrics(consent_scheduler_accuracy=accuracy)
    if scores and accuracy == 1.0:
        return SuiteResult("passed", metrics, scores)
    return SuiteResult("failed", metrics, scores, "the consent scheduler must be 100 % right")
