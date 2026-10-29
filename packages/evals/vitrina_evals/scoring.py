"""Scoring one conversation case and summarizing a run (AS-7.1, AS-7.2; plan.md §4.1).

The rules are the engine's own, applied from outside: an amount in the reply is invented
unless an active item the reply cites carries it, and so is a yes to an amount or a
promotion the customer named (R2; amounts read by the shared money normalizer, the yes by
the engine's own reading). Every reply of a case is checked for invented prices, and
``invented_price_rate`` is the share of replies with one. The last reply is checked against
the rest of what the case expects: a grounded reply cites every item the case expects and
nothing inactive (R3); a handoff must happen exactly when the case says, with its reason
(R5); an off-topic question is declined and a shop question is not (R10).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from vitrina_conversation.api import Agreement, agreed_offers
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
    replies: int = 1
    invented_replies: int = 0
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


def score_case(
    case: ConversationCase,
    scene: Scene,
    outcome: TurnOutcome,
    *,
    earlier: Sequence[TurnOutcome] = (),
) -> CaseScore:
    """Score the last reply, ``outcome``; ``earlier`` replies are checked for invented prices."""
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

    turns = (*earlier, outcome)
    invented: set[int] = set()
    invented_replies = 0
    for number, (message, turn) in enumerate(
        zip(case.messages[len(case.messages) - len(turns) :], turns, strict=True), start=1
    ):
        stated, agreed = _invented(scene, message, turn, expected.forbidden_amounts)
        where = f"reply {number}: " if number < len(turns) else ""
        if stated:
            problems.append(
                f"{where}states amounts no cited item supports: " + ", ".join(map(str, stated))
            )
        if agreed.labels():
            problems.append(
                f"{where}says yes to the customer's price no cited item supports: "
                + ", ".join(agreed.labels())
            )
        invented |= {*stated, *agreed.amounts}
        invented_replies += bool(stated or agreed.labels())
    found = amounts_cop(outcome.reply)

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
        invented_amounts=tuple(sorted(invented)),
        replies=len(turns),
        invented_replies=invented_replies,
        handoff_expected=handoff_expected,
        handoff_predicted=predicted,
        off_topic_expected=expected.off_topic,
        refused=outcome.off_topic,
        latency_ms=outcome.latency_ms,
    )


def _invented(
    scene: Scene, message: str, outcome: TurnOutcome, forbidden: Sequence[int]
) -> tuple[tuple[int, ...], Agreement]:
    """The amounts one reply states and the customer's prices it says yes to, unsupported."""
    quotable = set().union(
        *(scene.allowed.get(item_id, frozenset()) for item_id in outcome.cited_ids)
    )
    stated = sorted(
        {
            amount
            for amount in amounts_cop(outcome.reply)
            if amount not in quotable or amount in forbidden
        }
    )
    texts = [scene.texts[item_id] for item_id in outcome.cited_ids if item_id in scene.texts]
    return tuple(stated), agreed_offers(outcome.reply, message, quotable, texts)


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
        sum(s.invented_replies for s in scored), sum(s.replies for s in scored)
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
