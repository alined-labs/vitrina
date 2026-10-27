"""Briefs router. Owner: MX0 (plan.md §3).

Contract paths: /briefs, /briefs/generate, /briefs/{brief_id}, /briefs/{brief_id}/apply.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Briefs"])
