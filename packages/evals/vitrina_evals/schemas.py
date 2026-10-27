"""Eval runs and metrics as the HTTP contract defines them (openapi.yaml ``EvalRun``,
``EvalMetrics``, ``MetricsSummary``; FR-070)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

EvalSuite = Literal["conversations_es", "fidelity", "consent_scheduler", "price_validator"]
RunStatus = Literal["queued", "running", "passed", "failed", "error"]


class EvalMetrics(BaseModel):
    """AS-7.1. A metric is left out when the run had no case to measure it on."""

    groundedness_rate: float | MISSING = MISSING
    invented_price_rate: float | MISSING = MISSING
    handoff_precision: float | MISSING = MISSING
    handoff_recall: float | MISSING = MISSING
    brand_voice_mean: float | MISSING = MISSING
    verifier_pass_rate: float | MISSING = MISSING
    verifier_block_rate_on_altered: float | MISSING = MISSING
    off_topic_refusal_rate: float | MISSING = MISSING
    consent_scheduler_accuracy: float | MISSING = MISSING
    latency_p50_ms: int | MISSING = MISSING
    latency_p95_ms: int | MISSING = MISSING
    cost_usd_total: float | MISSING = MISSING


class EvalRunCreate(BaseModel):
    suite: EvalSuite
    replay: Annotated[
        bool, Field(description="Use recorded model outputs; no credentials needed")
    ] = True
    sample: Annotated[int, Field(ge=1, description="Run a subset")] | MISSING = MISSING


class EvalCaseResult(BaseModel):
    case_id: str | MISSING = MISSING
    passed: bool | MISSING = MISSING
    expected: dict[str, Any] | MISSING = MISSING
    actual: dict[str, Any] | MISSING = MISSING
    notes: str | MISSING = MISSING


class EvalRun(BaseModel):
    id: UUID
    suite: EvalSuite
    replay: bool
    status: RunStatus
    case_count: int | MISSING = MISSING
    metrics: EvalMetrics | MISSING = MISSING
    cases: list[EvalCaseResult] | MISSING = MISSING
    git_sha: str | MISSING = MISSING
    started_at: datetime
    finished_at: datetime | None = None


class LatestEval(BaseModel):
    run_id: UUID | MISSING = MISSING
    finished_at: datetime | MISSING = MISSING
    metrics: EvalMetrics | MISSING = MISSING


class LiveCounters(BaseModel):
    """Counters other modules own; each appears once its module reports it."""

    conversations_today: int | MISSING = MISSING
    attributed_rate: float | MISSING = MISSING
    handoffs_open: int | MISSING = MISSING
    blocked_sends_today: int | MISSING = MISSING
    creatives_blocked_total: int | MISSING = MISSING
    estimated_cost_per_business_usd: float | MISSING = MISSING


class MetricsSummary(BaseModel):
    latest_eval: LatestEval
    live: LiveCounters
