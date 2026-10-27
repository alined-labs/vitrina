"""Contacts & Consent router. Owner: MX0 (plan.md §3).

Contract paths: /contacts, /contacts/{contact_id} and its /consents, /opt-out.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Contacts & Consent"])
