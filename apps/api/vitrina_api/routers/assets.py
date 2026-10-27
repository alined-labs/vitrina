"""Assets router. Owner: MX1 (plan.md §3).

Contract paths: /assets, /assets/{asset_id}.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Assets"])
