"""Reactivation router. Owner: MX0 (plan.md §3).

Contract paths: /reactivation/campaigns, /reactivation/campaigns/{campaign_id} and its /send, /reactivation/eligibility.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Reactivation"])
