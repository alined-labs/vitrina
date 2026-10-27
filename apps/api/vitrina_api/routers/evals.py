"""Evals & Metrics router. Owner: MX0 (plan.md §3).

Contract paths: /evals/runs, /evals/runs/{run_id}, /metrics/summary, /metrics/costs.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from vitrina_core.auth import SellerId
from vitrina_core.http import FieldError, ProblemCode, ProblemError
from vitrina_core.ledger import CostReader, CostReport

router = APIRouter(tags=["Evals & Metrics"])


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
