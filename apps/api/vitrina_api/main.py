"""Application factory: every router is mounted under the contract's base path ``/v1``."""

from importlib.metadata import version

from fastapi import FastAPI

from vitrina_api.routers import (
    ads,
    agent,
    analytics,
    assets,
    briefs,
    catalog,
    contacts,
    conversations,
    creative,
    evals,
    health,
    jobs,
    public,
    reactivation,
    replay,
    sellers,
    webhooks,
)
from vitrina_core.settings import Settings, get_settings
from vitrina_local_mock import MockOutbox, create_phone_router

API_PREFIX = "/v1"

# Contract tag order (openapi.yaml `tags`).
ROUTERS = (
    sellers,
    catalog,
    assets,
    creative,
    ads,
    webhooks,
    conversations,
    agent,
    contacts,
    analytics,
    briefs,
    reactivation,
    evals,
    replay,
    public,
    jobs,
    health,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Vitrina API", version=version("vitrina-api"))
    for module in ROUTERS:
        app.include_router(module.router, prefix=API_PREFIX)
    if settings.whatsapp_mock:
        # Outside the contract and hidden from OpenAPI (plan.md §4.6, ADR-0011).
        app.include_router(create_phone_router(MockOutbox(settings.mock_outbox_path)))
    return app
