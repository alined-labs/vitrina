"""Ads router. Owner: MX1 (plan.md §3).

Contract paths: /ads, /ads/{ad_id}, /ads/{ad_id}/approve, /ads/{ad_id}/export, /ads/by-code/{code}.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Ads"])
