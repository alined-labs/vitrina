"""A conversations_es run passes only at spec.md §7's targets; failures here are injected."""

from pathlib import Path
from typing import Literal

import pytest

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import ProviderMode, Providers, compose
from vitrina_core.settings import Settings
from vitrina_evals import __main__ as cli
from vitrina_evals import suites
from vitrina_evals.cases import ConversationCase, fixture_id, load_cases
from vitrina_evals.runner import AgentTurn, SuiteResult, run_conversations
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import TurnOutcome
from vitrina_evals.targets import CONVERSATION_TARGETS, missed_targets

Fault = Literal["no_citation", "no_handoff", "needless_handoff"]


def grounded() -> list[ConversationCase]:
    return [case for case in load_cases() if case.expected.cites]


def handoffs() -> list[ConversationCase]:
    return [case for case in load_cases() if case.expected.handoff not in (False, None)]


def calm() -> list[ConversationCase]:
    return [case for case in load_cases() if case.expected.handoff is False]


def right_answer(case: ConversationCase) -> TurnOutcome:
    """What the case expects, and no amount, so only the injected fault can cost it anything."""
    handoff = case.expected.handoff
    return TurnOutcome(
        reply="Con mucho gusto.",
        cited_ids=tuple(fixture_id(case.business, key) for key in case.expected.cites),
        handoff_reason=handoff if isinstance(handoff, str) else "other" if handoff else None,
        off_topic=case.expected.off_topic,
    )


def wrong_answer(case: ConversationCase, fault: Fault) -> TurnOutcome:
    right = right_answer(case)
    if fault == "no_citation":
        return TurnOutcome(right.reply, (), right.handoff_reason, right.off_topic)
    if fault == "no_handoff":
        return TurnOutcome(right.reply, right.cited_ids, None, right.off_topic)
    return TurnOutcome(right.reply, right.cited_ids, "other", right.off_topic)


class InjectingAgent:
    """Answers each case as its fixture expects, except the cases given a fault.

    The runner goes through the cases and their messages in order, so the turns line up.
    """

    def __init__(
        self, cases: list[ConversationCase], faults: dict[str, Fault] | None = None
    ) -> None:
        faults = faults or {}
        unknown = faults.keys() - {case.case_id for case in cases}
        assert not unknown, f"a fault for a case the run does not have: {unknown}"
        self.answers = iter(
            wrong_answer(case, faults[case.case_id])
            if case.case_id in faults and last
            else right_answer(case)
            if last
            else TurnOutcome(reply="Con mucho gusto.")
            for case in cases
            for last in [False] * (len(case.messages) - 1) + [True]
        )

    async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
        return next(self.answers)


class Useless:
    async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
        return TurnOutcome(reply="Con mucho gusto.")


def replay(tmp_path: Path) -> tuple[Providers, InMemoryLedger]:
    ledger = InMemoryLedger()
    settings = Settings(_env_file=None, cassette_dir=tmp_path)  # type: ignore[call-arg]
    return compose(settings, ledger, mode=ProviderMode.replay), ledger


async def run(
    tmp_path: Path, cases: list[ConversationCase], faults: dict[str, Fault] | None = None
) -> SuiteResult:
    providers, ledger = replay(tmp_path)
    agent = InjectingAgent(cases, faults)
    return await run_conversations(cases, agent=agent, providers=providers, ledger=ledger)


def faults(cases: list[ConversationCase], count: int, fault: Fault) -> dict[str, Fault]:
    assert len(cases) >= count
    return {case.case_id: fault for case in cases[:count]}


def test_as_7_1_the_targets_are_the_specs() -> None:
    # spec.md §7. The invented-price rate has its own rule (AS-7.2): any one fails the run.
    assert CONVERSATION_TARGETS == {
        "groundedness_rate": 0.95,
        "handoff_precision": 0.9,
        "handoff_recall": 0.9,
    }


async def test_as_7_1_the_whole_suite_answered_right_passes(tmp_path: Path) -> None:
    result = await run(tmp_path, load_cases())

    assert len(result.cases) == 60
    assert all(score.passed for score in result.cases), [s.notes for s in result.cases]
    assert result.status == "passed", result.notes
    assert result.metrics.groundedness_rate == 1.0


async def test_as_7_1_forcing_all_sixty_cases_to_fail_fails_the_run(tmp_path: Path) -> None:
    # Every case gets a reply it did not want: nothing cited, no handoff, nothing declined.
    cases = load_cases()
    providers, ledger = replay(tmp_path)

    result = await run_conversations(cases, agent=Useless(), providers=providers, ledger=ledger)

    assert result.status == "failed"
    assert result.metrics.invented_price_rate == 0.0  # no invented price: the targets fail it
    assert "groundedness_rate" in result.notes
    assert "handoff_recall" in result.notes


async def test_as_7_1_groundedness_exactly_at_the_target_passes_and_below_fails(
    tmp_path: Path,
) -> None:
    twenty = grounded()[:20]

    at = await run(tmp_path, twenty, faults(twenty, 1, "no_citation"))
    below = await run(tmp_path, twenty, faults(twenty, 2, "no_citation"))

    assert at.metrics.groundedness_rate == 0.95
    assert at.status == "passed", at.notes
    assert below.metrics.groundedness_rate == 0.9
    assert below.status == "failed"
    assert "groundedness_rate 0.9 is below the target 0.95" in below.notes


async def test_as_7_1_handoff_recall_exactly_at_the_target_passes_and_below_fails(
    tmp_path: Path,
) -> None:
    ten = handoffs()[:10]

    at = await run(tmp_path, ten, faults(ten, 1, "no_handoff"))
    below = await run(tmp_path, ten, faults(ten, 2, "no_handoff"))

    assert at.metrics.handoff_recall == 0.9
    assert at.status == "passed", at.notes
    assert below.metrics.handoff_recall == 0.8
    assert below.status == "failed"
    assert "handoff_recall 0.8 is below the target 0.9" in below.notes


async def test_as_7_1_handoff_precision_exactly_at_the_target_passes_and_below_fails(
    tmp_path: Path,
) -> None:
    at_cases = handoffs()[:9] + calm()[:1]
    below_cases = handoffs()[:8] + calm()[:2]

    at = await run(tmp_path, at_cases, faults(at_cases[9:], 1, "needless_handoff"))
    below = await run(tmp_path, below_cases, faults(below_cases[8:], 2, "needless_handoff"))

    assert at.metrics.handoff_precision == 0.9
    assert at.status == "passed", at.notes
    assert below.metrics.handoff_precision == 0.8
    assert below.status == "failed"
    assert "handoff_precision 0.8 is below the target 0.9" in below.notes


async def test_as_7_1_the_sixty_cases_fail_as_soon_as_a_target_is_missed(tmp_path: Path) -> None:
    cases = load_cases()

    tolerated = await run(tmp_path, cases, faults(grounded(), 1, "no_citation"))
    missed = await run(tmp_path, cases, faults(grounded(), 2, "no_citation"))

    assert tolerated.metrics.groundedness_rate == round(28 / 29, 4)
    assert tolerated.status == "passed", tolerated.notes
    assert missed.metrics.groundedness_rate == round(27 / 29, 4)
    assert missed.status == "failed"


async def test_as_7_2_an_invented_price_still_fails_a_run_that_meets_every_target(
    tmp_path: Path,
) -> None:
    cases = load_cases()
    providers, ledger = replay(tmp_path)
    agent = InjectingAgent(cases)
    answers = list(agent.answers)
    answers[-1] = TurnOutcome(reply="Te sale en $1.000.", cited_ids=answers[-1].cited_ids)
    agent.answers = iter(answers)

    result = await run_conversations(cases, agent=agent, providers=providers, ledger=ledger)

    assert result.status == "failed"
    assert "AS-7.2" in result.notes


def test_as_7_1_a_metric_the_run_had_no_case_for_is_not_a_miss() -> None:
    assert missed_targets(EvalMetrics()) == []


async def test_as_7_1_no_suite_presents_brand_voice_as_measured(tmp_path: Path) -> None:
    # spec.md §7 sets a target, but no suite computes the mean: it must stay absent, not 0 or a
    # guess, and it is not one of the targets a run can be said to meet.
    result = await run(tmp_path, load_cases())

    assert "brand_voice_mean" not in result.metrics.model_dump(mode="json")
    assert "brand_voice_mean" not in CONVERSATION_TARGETS


def test_as_7_1_make_evals_exits_1_below_a_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(suites, "conversation_agent", lambda _settings: Useless())

    assert cli.main(["--suite", "conversations_es"]) == 1
    assert "below the target" in capsys.readouterr().out
