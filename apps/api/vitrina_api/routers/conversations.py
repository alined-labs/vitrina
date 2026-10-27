"""Conversations router. Owner: MX2 (plan.md §3).

Contract paths: /conversations, /conversations/{conversation_id} and its /messages, /takeover, /release.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Conversations"])
