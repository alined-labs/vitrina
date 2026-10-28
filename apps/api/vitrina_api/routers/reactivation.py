"""Reactivation router (T064).

Contract paths: /reactivation/campaigns, /reactivation/campaigns/{campaign_id} and its /send,
/reactivation/eligibility. Campaigns live in Postgres: without DATABASE_URL every route
answers 503.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import MISSING, BaseModel

from vitrina_compliance.api import (
    CAMPAIGN_SEND,
    CampaignConflictError,
    CampaignDeps,
    ReactivationCampaign,
    ReactivationCampaignCreate,
    SendDecision,
    check_eligibility,
    preview_campaign,
    schedule_send,
)
from vitrina_conversation.api import campaign_deps
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)

router = APIRouter(tags=["Reactivation"])
Paging = Annotated[PageQuery, Depends()]


class EligibilityCheck(BaseModel):
    contact_id: UUID
    at: datetime | MISSING = MISSING


def deps(request: Request) -> CampaignDeps:
    state = request.app.state
    if state.sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE, detail="campaigns need the database (DATABASE_URL)"
        )
    return campaign_deps(state.sessions, state.catalog, state.compliance)


Campaigns = Annotated[CampaignDeps, Depends(deps)]


def _not_found(campaign_id: UUID) -> ProblemError:
    return ProblemError(ProblemCode.NOT_FOUND, detail=f"no campaign {campaign_id} for this seller")


@router.post(
    "/reactivation/campaigns",
    status_code=201,
    operation_id="createReactivationCampaign",
    summary="Build a campaign preview with per-contact eligibility decisions (nothing is sent)",
    response_description="Campaign preview",
)
async def create_campaign(
    seller_id: SellerId,
    campaigns: Campaigns,
    data: ReactivationCampaignCreate,
    _key: IdempotencyKey = None,
) -> ReactivationCampaign:
    return await preview_campaign(campaigns, seller_id, data, now=datetime.now(UTC))


@router.get(
    "/reactivation/campaigns",
    operation_id="listReactivationCampaigns",
    summary="List campaigns",
    response_description="Page of campaigns",
)
async def list_campaigns(
    seller_id: SellerId, campaigns: Campaigns, page: Paging
) -> Page[ReactivationCampaign]:
    items, cursor = await campaigns.store.page(seller_id, limit=page.limit, cursor=page.cursor)
    return Page[ReactivationCampaign](items=items, next_cursor=cursor)


@router.get(
    "/reactivation/campaigns/{campaign_id}",
    operation_id="getReactivationCampaign",
    summary="Get a campaign with its send decisions",
    response_description="Campaign",
    responses=problem_responses(404),
)
async def get_campaign(
    seller_id: SellerId, campaigns: Campaigns, campaign_id: UUID
) -> ReactivationCampaign:
    campaign = await campaigns.store.get(seller_id, campaign_id)
    if campaign is None:
        raise _not_found(campaign_id)
    return campaign


@router.post(
    "/reactivation/campaigns/{campaign_id}/send",
    status_code=202,
    operation_id="sendReactivationCampaign",
    summary="Send to `allowed` contacts only, via approved template, inside legal hours",
    description=(
        "Re-evaluates eligibility at send time; `deferred` contacts are scheduled for "
        "`next_allowed_at`."
    ),
    response_description="Sending scheduled",
    responses=problem_responses(
        409,
        description_409=(
            "Template not approved or campaign already sent "
            "(`TEMPLATE_NOT_APPROVED`, `CAMPAIGN_ALREADY_SENT`)"
        ),
    ),
)
async def send_campaign(
    request: Request,
    seller_id: SellerId,
    campaigns: Campaigns,
    campaign_id: UUID,
    _key: IdempotencyKey = None,
) -> ReactivationCampaign:
    seller = await request.app.state.sellers.get(seller_id)
    approved = seller is not None and seller.marketing_template_status == "approved"
    try:
        campaign = await schedule_send(
            campaigns.store, seller_id, campaign_id, template_approved=approved
        )
    except LookupError as error:
        raise _not_found(campaign_id) from error
    except CampaignConflictError as conflict:
        raise ProblemError(ProblemCode(conflict.code), detail=conflict.detail) from conflict
    now = datetime.now(UTC)
    later = (
        campaign.scheduled_for if campaign.scheduled_for and campaign.scheduled_for > now else None
    )
    async with request.app.state.sessions() as session, session.begin():
        await request.app.state.jobs.enqueue(
            session,
            kind="campaign_send",
            task=CAMPAIGN_SEND,
            payload={"campaign_id": str(campaign_id)},
            owner_seller_id=seller_id,
            idempotency_key=f"campaign_send:{campaign_id}",
            run_after=later,
        )
    return campaign


@router.post(
    "/reactivation/eligibility",
    operation_id="checkEligibility",
    summary="Evaluate one contact's eligibility now (debug / demo)",
    response_description="Decision",
)
async def eligibility(
    seller_id: SellerId, campaigns: Campaigns, check: EligibilityCheck
) -> SendDecision:
    at = check.at if isinstance(check.at, datetime) else datetime.now(UTC)
    decision = await check_eligibility(campaigns, seller_id, check.contact_id, at=at)
    if decision is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no contact {check.contact_id}")
    return decision
