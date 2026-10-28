"""Health router.

Contract paths: /health, /health/providers.
"""

from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any, Literal

import httpx2
from fastapi import APIRouter, Request
from pydantic import BaseModel

from vitrina_core.auth import SellerId
from vitrina_core.health import (
    Check,
    HealthCache,
    Outcome,
    ProviderHealth,
    database_check,
    fixed,
    http_check,
    mocked,
    queue_check,
    reachable,
    run_checks,
)
from vitrina_core.jobs import STALLED_AFTER_S, PostgresJobQueue
from vitrina_core.providers.compose import ProviderMode
from vitrina_core.settings import Settings
from vitrina_meta_model_api.health import meta_check
from vitrina_whatsapp.config import DEFAULT_GRAPH_VERSION, GRAPH_BASE_URL

router = APIRouter(tags=["Health"])


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@router.get("/health", operation_id="getHealth", summary="Liveness")
def get_health() -> Health:
    return Health(status="ok", version=version("vitrina-api"))


def provider_checks(state: Any, http: httpx2.AsyncClient) -> dict[str, Check]:
    """One check per dependency, as this process is configured (NFR-02)."""
    settings: Settings = state.settings
    replay = state.providers.mode == ProviderMode.replay
    checks: dict[str, Check] = {
        "meta_model_api": (
            mocked("replay mode: recorded cassettes")
            if replay
            else meta_check(settings, timeout_s=settings.health_check_timeout_s)
        ),
        "whatsapp": whatsapp_check(settings, http),
    }
    if settings.modal_glimmer_url:
        url = settings.modal_glimmer_url
        checks["modal"] = mocked("replay mode") if replay else (lambda: reachable(http, url))
    if state.sessions is None:
        checks["database"] = mocked("in-memory stores: no DATABASE_URL")
        checks["queue"] = mocked("in-memory: jobs need DATABASE_URL")
    else:
        checks["database"] = database_check(state.sessions)
        checks["queue"] = queue_check(
            PostgresJobQueue(state.sessions), stalled_after_s=STALLED_AFTER_S
        )
    return checks


def whatsapp_check(settings: Settings, http: httpx2.AsyncClient) -> Check:
    if settings.whatsapp_mock:
        return mocked("local mock (WHATSAPP_MOCK)")
    token, number = settings.whatsapp_access_token, settings.whatsapp_phone_number_id
    if token is None or not number:
        return fixed(
            Outcome("down", "WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is not set")
        )
    # The phone number resource, id only: proves the token and the number, returns no digits.
    url = f"{GRAPH_BASE_URL}/{DEFAULT_GRAPH_VERSION}/{number}?fields=id"
    headers = {"authorization": f"Bearer {token.get_secret_value()}"}
    return lambda: http_check(http, url, headers=headers)


async def report(state: Any) -> dict[str, ProviderHealth]:
    settings: Settings = state.settings
    async with httpx2.AsyncClient(timeout=settings.health_check_timeout_s) as http:
        return await run_checks(
            provider_checks(state, http),
            timeout_s=settings.health_check_timeout_s,
            now=lambda: datetime.now(UTC),
        )


@router.get(
    "/health/providers",
    operation_id="getProviderHealth",
    summary="Reachability and latency of Meta Model API, WhatsApp Cloud API, Modal, DB and queue",
)
async def get_provider_health(_seller: SellerId, request: Request) -> dict[str, ProviderHealth]:
    cache: HealthCache = request.app.state.health_cache
    return await cache.get(lambda: report(request.app.state))
