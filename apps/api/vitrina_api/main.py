"""Application factory: every router is mounted under the contract's base path ``/v1``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from vitrina_core.db import create_engine, create_session_factory
from vitrina_core.http import (
    IdempotencyMiddleware,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PostgresIdempotencyStore,
    RequestIdMiddleware,
    install_problem_handlers,
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
    # Without DATABASE_URL (replay, CI's check job) the app still starts; DB routes cannot.
    engine = create_engine(settings) if settings.database_url and settings.db_schema else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Vitrina API", version=version("vitrina-api"), lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = create_session_factory(engine) if engine is not None else None
    install_problem_handlers(app)
    for module in ROUTERS:
        app.include_router(module.router, prefix=API_PREFIX)
    if settings.whatsapp_mock:
        # Outside the contract and hidden from OpenAPI (plan.md §4.6, ADR-0011).
        app.include_router(create_phone_router(MockOutbox(settings.mock_outbox_path)))

    store: IdempotencyStore = (
        PostgresIdempotencyStore(app.state.sessions)
        if app.state.sessions
        else InMemoryIdempotencyStore()
    )
    # Keys are scoped to NIL until core.auth resolves the seller (T006).
    app.add_middleware(IdempotencyMiddleware, store=store)
    # Added last, so outermost: every response, errors included, carries X-Request-Id.
    app.add_middleware(RequestIdMiddleware)
    return app
