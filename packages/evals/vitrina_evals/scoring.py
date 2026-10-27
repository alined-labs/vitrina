"""Scoring one conversation case and summarizing a run (AS-7.1, AS-7.2; plan.md §4.1).

The rules are the engine's own, applied from outside: an amount in the reply is invented
unless an active item the reply cites carries it (R2; amounts read by the shared money
normalizer); a grounded reply cites every item the case expects and nothing inactive (R3);
a handoff must happen exactly when the case says, with its reason (R5); an off-topic
question is declined and a shop question is not (R10).
"""

import math
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from vitrina_core.money import amounts_cop
from vitrina_evals.cases import ConversationCase, Scene
from vitrina_evals.schemas import EvalCaseResult, EvalMetrics


@dataclass(frozen=True)
class TurnOutcome:
    """What the agent finally said in a turn, as the evals read it."""

    reply: str
    cited_ids: tuple[UUID, ...] = ()
    handoff_reason: str | None = None
    off_topic: bool = False
    latency_ms: int = 0


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    passed: bool
    notes: str
    expected: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)
    grounded: bool | None = None
    invented_amounts: tuple[int, ...] = ()
    handoff_expected: bool | None = None
    handoff_predicted: bool = False
    off_topic_expected: bool = False
    refused: bool = False
    latency_ms: int | None = None
    error: bool = False

    def result(self) -> EvalCaseResult:
        return EvalCaseResult(
            case_id=self.case_id,
            passed=self.passed,
            expected=self.expected,
            actual=self.actual,
            notes=self.notes,
        )


def score_case(case: ConversationCase, scene: Scene, outcome: TurnOutcome) -> CaseScore:
    expected = case.expected
    keys = {item_id: key for key, item_id in scene.ids.items()}
    cited = [keys.get(item_id, f"unknown:{item_id}") for item_id in outcome.cited_ids]
    problems: list[str] = []

    inactive = [
        key
        for key, item_id in zip(cited, outcome.cited_ids, strict=True)
        if item_id not in scene.allowed
    ]
    if inactive:
        problems.append("cites what the agent may not cite: " + ", ".join(inactive))
    forbidden = [key for key in expected.forbidden_cites if key in cited]
    if forbidden:
        problems.append("cites a forbidden item: " + ", ".join(forbidden))
    grounded: bool | None = None
    if expected.cites:
        missing = [key for key in expected.cites if key not in cited]
        grounded = not missing and not inactive
        if missing:
            problems.append("does not cite " + ", ".join(missing))

    quotable = set().union(
        *(scene.allowed.get(item_id, frozenset()) for item_id in outcome.cited_ids)
    )
    found = amounts_cop(outcome.reply)
    invented = tuple(
        sorted(
            {
                amount
                for amount in found
                if amount not in quotable or amount in expected.forbidden_amounts
            }
        )
    )
    if invented:
        problems.append("states amounts no cited item supports: " + ", ".join(map(str, invented)))

    predicted = outcome.handoff_reason is not None
    handoff_expected: bool | None = None
    if expected.handoff is not None:
        handoff_expected = expected.handoff is not False
        if handoff_expected != predicted:
            problems.append("hands off" if predicted else "does not hand off")
        elif isinstance(expected.handoff, str) and outcome.handoff_reason != expected.handoff:
            problems.append(f"hands off for {outcome.handoff_reason}, expected {expected.handoff}")

    if expected.off_topic and not outcome.off_topic:
        problems.append("answers an off-topic question")
    if outcome.off_topic and not expected.off_topic:
        problems.append("declines a question about the shop")

    return CaseScore(
        case_id=case.case_id,
        passed=not problems,
        notes="; ".join(problems) or "ok",
        expected=expected.model_dump(mode="json", exclude_defaults=True),
        actual={
            "reply": outcome.reply,
            "cites": cited,
            "handoff_reason": outcome.handoff_reason,
            "off_topic": outcome.off_topic,
            "amounts": found,
        },
        grounded=grounded,
        invented_amounts=invented,
        handoff_expected=handoff_expected,
        handoff_predicted=predicted,
        off_topic_expected=expected.off_topic,
        refused=outcome.off_topic,
        latency_ms=outcome.latency_ms,
    )


def error_case(case: ConversationCase, message: str) -> CaseScore:
    return CaseScore(
        case_id=case.case_id,
        passed=False,
        notes=f"error: {message}",
        expected=case.expected.model_dump(mode="json", exclude_defaults=True),
        error=True,
    )


def summarize(scores: list[CaseScore], *, cost_usd: float) -> EvalMetrics:
    scored = [score for score in scores if not score.error]
    metrics: dict[str, Any] = {"cost_usd_total": round(cost_usd, 6)}
    if not scored:
        return EvalMetrics(**metrics)
    grounded = [score.grounded for score in scored if score.grounded is not None]
    if grounded:
        metrics["groundedness_rate"] = _rate(sum(grounded), len(grounded))
    metrics["invented_price_rate"] = _rate(
        sum(bool(s.invented_amounts) for s in scored), len(scored)
    )
    labelled = [score for score in scored if score.handoff_expected is not None]
    true_positive = sum(bool(s.handoff_expected) and s.handoff_predicted for s in labelled)
    predicted = sum(s.handoff_predicted for s in labelled)
    actual = sum(bool(s.handoff_expected) for s in labelled)
    if predicted:
        metrics["handoff_precision"] = _rate(true_positive, predicted)
    if actual:
        metrics["handoff_recall"] = _rate(true_positive, actual)
    off_topic = [score for score in scored if score.off_topic_expected]
    if off_topic:
        metrics["off_topic_refusal_rate"] = _rate(sum(s.refused for s in off_topic), len(off_topic))
    latencies = sorted(score.latency_ms for score in scored if score.latency_ms is not None)
    if latencies:
        metrics["latency_p50_ms"] = _nearest_rank(latencies, 0.50)
        metrics["latency_p95_ms"] = _nearest_rank(latencies, 0.95)
    return EvalMetrics(**metrics)


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4)


def _nearest_rank(ordered: list[int], quantile: float) -> int:
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]
