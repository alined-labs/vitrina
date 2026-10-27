"""Replay router. Owner: MX0 (plan.md §3).

Contract paths: /replay/fixtures, /replay/sessions.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Replay"])
