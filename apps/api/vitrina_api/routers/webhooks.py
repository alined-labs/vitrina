"""Webhooks router. Owner: MX2 (plan.md §3).

Contract paths: /webhooks/whatsapp.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Webhooks"])
