"""Public interface of the evals module (constitution Art. VI.3; T028).

Suites run in the worker (``evals.run``, FR-070) or in-process with ``make evals``; their
results are ``EvalRun``s, and the latest finished one feeds ``GET /metrics/summary``. The
conversations_es suite evaluates any ``Agent``: the conversation engine once it has
``run_turn``.
"""

from vitrina_evals.cases import ConversationCase, Expected, Scene, build_scene, load_cases
from vitrina_evals.live import METRICS_ENTRY_POINT, Counters, live_counters, load_counters
from vitrina_evals.runner import Agent, AgentTurn, Exchange, SuiteResult, run_conversations
from vitrina_evals.schemas import (
    EvalCaseResult,
    EvalMetrics,
    EvalRun,
    EvalRunCreate,
    EvalSuite,
    LatestEval,
    LiveCounters,
    MetricsSummary,
)
from vitrina_evals.scoring import TurnOutcome
from vitrina_evals.store import EvalRunStore, InMemoryEvalRuns, PostgresEvalRuns
from vitrina_evals.suites import execute
from vitrina_evals.tasks import EVAL_TASK

__all__ = [
    "EVAL_TASK",
    "METRICS_ENTRY_POINT",
    "Agent",
    "AgentTurn",
    "ConversationCase",
    "Counters",
    "EvalCaseResult",
    "EvalMetrics",
    "EvalRun",
    "EvalRunCreate",
    "EvalRunStore",
    "EvalSuite",
    "Exchange",
    "Expected",
    "InMemoryEvalRuns",
    "LatestEval",
    "LiveCounters",
    "MetricsSummary",
    "PostgresEvalRuns",
    "Scene",
    "SuiteResult",
    "TurnOutcome",
    "build_scene",
    "execute",
    "live_counters",
    "load_cases",
    "load_counters",
    "run_conversations",
]
