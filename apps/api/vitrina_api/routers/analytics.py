"""Analytics router.

Contract paths: /analytics/attribution, /analytics/objections.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request

from vitrina_core.auth import SellerId
from vitrina_core.http import FieldError, ProblemCode, ProblemError
from vitrina_creative.api import AdStore
from vitrina_learning.api import (
    AttributionReport,
    CreativeAds,
    ObjectionReport,
    attribution_report,
    objection_report,
)

router = APIRouter(tags=["Analytics"])

From = Annotated[date, Query(alias="from")]
To = Annotated[date, Query(alias="to")]


def _check(period_from: date, period_to: date) -> None:
    if period_from > period_to:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="the period ends before it starts",
            errors=[FieldError(field="to", message="must be on or after from")],
        )


@router.get(
    "/analytics/attribution",
    operation_id="getAttribution",
    summary="Ad → chat → outcome funnel per ad and per angle",
    response_description="Attribution report",
)
async def get_attribution(
    seller_id: SellerId, request: Request, period_from: From, period_to: To
) -> AttributionReport:
    _check(period_from, period_to)
    sessions = request.app.state.sessions
    if sessions is None:  # in-memory mode keeps no attribution events
        return AttributionReport(period_from=period_from, period_to=period_to, ads=[], by_angle={})
    ads = CreativeAds(AdStore(sessions))
    return await attribution_report(sessions, seller_id, period_from, period_to, ads=ads)


@router.get(
    "/analytics/objections",
    operation_id="getObjections",
    summary="Objection tags per ad and period",
    response_description="Objection report",
)
async def get_objections(
    seller_id: SellerId, request: Request, period_from: From, period_to: To
) -> ObjectionReport:
    _check(period_from, period_to)
    sessions = request.app.state.sessions
    if sessions is None:
        return ObjectionReport(period_from=period_from, period_to=period_to, totals={}, by_ad=[])
    ads = CreativeAds(AdStore(sessions))
    return await objection_report(sessions, seller_id, period_from, period_to, ads=ads)
