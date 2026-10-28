"""Briefs router.

Contract paths: /briefs, /briefs/generate, /briefs/{brief_id}, /briefs/{brief_id}/apply.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Briefs"])
