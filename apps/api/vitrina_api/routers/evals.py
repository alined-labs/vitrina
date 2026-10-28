"""Evals & Metrics router. Owner: MX0 (plan.md §3).

Contract paths: /evals/runs, /evals/runs/{run_id}, /metrics/summary, /metrics/costs.
"""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import MISSING

from vitrina_core.auth import SellerId, SellerOrService
from vitrina_core.http import (
    FieldError,
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
)
from vitrina_core.jobs import PostgresJobQueue
from vitrina_core.ledger import CostReader, CostReport
from vitrina_evals.api import (
    EVAL_TASK,
    EvalRun,
    EvalRunCreate,
    EvalRunStore,
    EvalSuite,
    LatestEval,
    MetricsSummary,
    live_counters,
)

router = APIRouter(tags=["Evals & Metrics"])


def eval_runs(request: Request) -> EvalRunStore:
    store: EvalRunStore = request.app.state.eval_runs
    return store


Runs = Annotated[EvalRunStore, Depends(eval_runs)]
Paging = Annotated[PageQuery, Depends()]


@router.post(
    "/evals/runs",
    status_code=202,
    operation_id="createEvalRun",
    summary="Run an evaluation suite",
    response_description="Run started",
)
async def create_eval_run(
    request: Request,
    _caller: SellerOrService,
    runs: Runs,
    body: EvalRunCreate,
    _key: IdempotencyKey = None,
) -> EvalRun:
    jobs: PostgresJobQueue | None = request.app.state.jobs
    if jobs is None or request.app.state.sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail="eval runs need the database (DATABASE_URL); `make evals` runs one without",
        )
    payload: dict[str, object] = {}
    if body.sample is not MISSING:
        payload["sample"] = body.sample
    # The run and the job that executes it commit together. Runs are deployment-wide, not
    # seller data, so the job has no owner and its model calls no seller (eval_runs).
    async with request.app.state.sessions() as session, session.begin():
        run = await runs.create(body.suite, replay=body.replay, session=session)
        await jobs.enqueue(
            session,
            kind="eval_run",
            task=EVAL_TASK,
            payload={"run_id": str(run.id), **payload},
            owner_seller_id=None,
        )
    return run


@router.get("/evals/runs", operation_id="listEvalRuns", summary="List eval runs")
async def list_eval_runs(
    _seller: SellerId, runs: Runs, page: Paging, suite: EvalSuite | None = None
) -> Page[EvalRun]:
    items, cursor = await runs.list_runs(limit=page.limit, cursor=page.cursor, suite=suite)
    return Page[EvalRun](items=items, next_cursor=cursor)


@router.get(
    "/evals/runs/{run_id}",
    operation_id="getEvalRun",
    summary="Get an eval run with metrics and per-case results",
    response_description="Run",
)
async def get_eval_run(_seller: SellerId, runs: Runs, run_id: UUID) -> EvalRun:
    run = await runs.get(run_id)
    if run is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no eval run {run_id}")
    return run


@router.get(
    "/metrics/summary",
    operation_id="getMetricsSummary",
    summary="Latest eval metrics plus live counters for the dashboard metrics page",
    response_description="Summary",
)
async def get_metrics_summary(seller_id: SellerId, runs: Runs, request: Request) -> MetricsSummary:
    state = request.app.state
    live = await live_counters(
        state.live_counters, state.sessions, seller_id, now=datetime.now(UTC)
    )
    latest = await runs.latest()
    if latest is None or latest.finished_at is None:
        return MetricsSummary(latest_eval=LatestEval(), live=live)
    return MetricsSummary(
        latest_eval=LatestEval(
            run_id=latest.id, finished_at=latest.finished_at, metrics=latest.metrics
        ),
        live=live,
    )


def cost_reader(request: Request) -> CostReader:
    reader: CostReader = request.app.state.ledger
    return reader


@router.get(
    "/metrics/costs",
    operation_id="getCosts",
    summary="Model and messaging cost ledger aggregated by provider/model",
)
async def get_costs(
    seller_id: SellerId,
    ledger: Annotated[CostReader, Depends(cost_reader)],
    period_from: Annotated[date, Query(alias="from")],
    period_to: Annotated[date, Query(alias="to")],
) -> CostReport:
    if period_from > period_to:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="the period ends before it starts",
            errors=[FieldError(field="to", message="must be on or after from")],
        )
    return await ledger.report(seller_id, period_from, period_to)
