"""Running the conversations_es suite against the agent (T028, AS-7.1, AS-7.3).

Each case gets a fresh scene, its messages go to the agent one by one with the earlier
exchanges as history, and the last reply is scored; every reply is checked for invented
prices. The providers the agent gets are the run's: replay ones answer from cassettes and
need no key. A case that raises is reported as an error and the others still run. The run
fails on any invented price in any reply (AS-7.2), is an error when a case could not be
scored, and passes otherwise.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, NamedTuple, Protocol

from vitrina_catalog.api import Catalog, SellerSettings
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import Providers
from vitrina_evals.cases import ConversationCase, build_scene, load_business
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import CaseScore, TurnOutcome, error_case, score_case, summarize

NO_AGENT = "no agent to evaluate: run_turn needs MODEL_AGENT (spec.md §8, Q01)"


class Exchange(NamedTuple):
    customer: str
    reply: str


@dataclass(frozen=True)
class AgentTurn:
    seller: SellerSettings
    catalog: Catalog
    providers: Providers
    history: tuple[Exchange, ...]
    message: str


class Agent(Protocol):
    async def run_turn(self, turn: AgentTurn) -> TurnOutcome: ...


@dataclass(frozen=True)
class SuiteResult:
    status: Literal["passed", "failed", "error"]
    metrics: EvalMetrics
    cases: list[CaseScore]
    notes: str = ""


async def run_conversations(
    cases: Sequence[ConversationCase],
    *,
    agent: Agent | None,
    providers: Providers,
    ledger: InMemoryLedger,
) -> SuiteResult:
    if agent is None:
        return SuiteResult("error", EvalMetrics(), [], NO_AGENT)
    scores = [await _run_case(case, agent, providers) for case in cases]
    cost = sum(entry.cost_usd for entry in ledger.entries if not entry.replayed)
    metrics = summarize(scores, cost_usd=cost)
    if any(score.invented_replies for score in scores):
        return SuiteResult("failed", metrics, scores, "an invented price fails the run (AS-7.2)")
    if not scores or any(score.error for score in scores):
        return SuiteResult("error", metrics, scores, "some cases could not be scored")
    return SuiteResult("passed", metrics, scores)


async def _run_case(case: ConversationCase, agent: Agent, providers: Providers) -> CaseScore:
    scene = await build_scene(load_business(case.business))
    history: list[Exchange] = []
    outcomes: list[TurnOutcome] = []
    try:
        for message in case.messages:
            started = time.perf_counter()
            outcome = await agent.run_turn(
                AgentTurn(scene.seller, scene.catalog, providers, tuple(history), message)
            )
            outcome = replace(outcome, latency_ms=round((time.perf_counter() - started) * 1000))
            history.append(Exchange(message, outcome.reply))
            outcomes.append(outcome)
    except Exception as error:  # one broken case must not hide the others
        return error_case(case, f"{type(error).__name__}: {error}")
    return score_case(case, scene, outcomes[-1], earlier=outcomes[:-1])
