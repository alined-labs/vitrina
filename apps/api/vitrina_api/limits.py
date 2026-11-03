"""Per-seller daily quotas on the routes a signed-in seller can spend model credit through.

Only routes call these. Customer conversations reach the models through the WhatsApp pipeline,
never through here, so they are neither blocked nor counted, and BAJA always works.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import Request

from vitrina_core.http import ProblemCode, ProblemError
from vitrina_core.quotas import UsageCounter, day_start, seconds_to_next_day
from vitrina_core.settings import Settings

Quota = Literal["agent_turns", "creative_jobs", "paid_jobs"]

REFUSALS: dict[Quota, str] = {
    "agent_turns": "Llegaste al límite de hoy para probar el agente. Vuelve a intentarlo mañana.",
    "creative_jobs": "Llegaste al límite de hoy para crear anuncios. Vuelve a intentarlo mañana.",
    "paid_jobs": "Llegaste al límite de hoy para este tipo de trabajo. Vuelve a intentarlo mañana.",
}


def _key(seller_id: UUID, quota: Quota) -> str:
    return f"seller:{seller_id}:{quota}"


async def take_quota(request: Request, seller_id: UUID, quota: Quota, amount: int = 1) -> None:
    """Count paid work the seller is about to start, or refuse it with 429 before it costs."""
    settings: Settings = request.app.state.settings
    usage: UsageCounter = request.app.state.usage
    limit: int = getattr(settings, f"seller_daily_{quota}")
    now = datetime.now(UTC)
    if not await usage.take(_key(seller_id, quota), day_start(now), limit=limit, amount=amount):
        raise ProblemError(
            ProblemCode.RATE_LIMITED,
            detail=REFUSALS[quota],
            headers={"Retry-After": str(seconds_to_next_day(now))},
        )


async def return_quota(request: Request, seller_id: UUID, quota: Quota) -> None:
    """For work refused after ``take_quota``, before it was queued."""
    usage: UsageCounter = request.app.state.usage
    await usage.give_back(_key(seller_id, quota), day_start(datetime.now(UTC)))
