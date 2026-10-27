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


def create_app() -> FastAPI:
    app = FastAPI(title="Vitrina API", version=version("vitrina-api"))
    for module in ROUTERS:
        app.include_router(module.router, prefix=API_PREFIX)
    return app
