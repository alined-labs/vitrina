"""Application factory: every router is mounted under the contract's base path ``/v1``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from vitrina_catalog.api import InMemoryCatalog
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_catalog.sellers import InMemorySellerStore, PostgresSellerStore
from vitrina_compliance.api import ContactIdentity, InMemoryCompliance, PostgresCompliance
from vitrina_core.auth import (
    Authenticator,
    PostgresSellerDirectory,
    SellerDirectory,
    TokenVerifier,
)
from vitrina_core.db import create_engine, create_session_factory
from vitrina_core.health import HealthCache
from vitrina_core.http import (
    BodyLimitMiddleware,
    IdempotencyMiddleware,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PostgresIdempotencyStore,
    RequestIdMiddleware,
    install_problem_handlers,
)
from vitrina_core.jobs import PostgresJobQueue
from vitrina_core.ledger import InMemoryLedger, PostgresLedger, SpendWatch
from vitrina_core.providers.compose import compose
from vitrina_core.settings import Settings, get_settings
from vitrina_core.storage import LocalBlobStore, blob_store, create_blob_router
from vitrina_creative.media import IMAGE_MAX_BYTES, VIDEO_MAX_BYTES
from vitrina_evals.api import InMemoryEvalRuns, PostgresEvalRuns, load_counters
from vitrina_local_mock import MockOutbox, create_phone_router
from vitrina_meta_model_api import live_providers
from vitrina_whatsapp.webhook import MAX_BODY_BYTES

API_PREFIX = "/v1"

# The largest body each upload route takes: its own file limit plus room for the multipart
# envelope. Every other body is JSON and gets BodyLimitMiddleware's 1 MB.
_ENVELOPE = 1024 * 1024
BODY_LIMITS = {
    f"{API_PREFIX}/assets": VIDEO_MAX_BYTES + _ENVELOPE,
    f"{API_PREFIX}/products/import": catalog.MAX_PHOTO_BYTES + _ENVELOPE,
    f"{API_PREFIX}/public/showcases": IMAGE_MAX_BYTES + _ENVELOPE,
    f"{API_PREFIX}/sellers/me/voice-samples": sellers.MAX_EXPORT_BYTES + _ENVELOPE,
    f"{API_PREFIX}/webhooks/whatsapp": MAX_BODY_BYTES + _ENVELOPE,
}

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


def authenticator(settings: Settings, directory: SellerDirectory) -> Authenticator:
    """Supabase tokens: HS256 with SUPABASE_JWT_SECRET, or the project keys under SUPABASE_URL."""
    auth_url = f"{settings.supabase_url.rstrip('/')}/auth/v1" if settings.supabase_url else None
    verifier = TokenVerifier(
        secret=settings.supabase_jwt_secret,
        issuer=auth_url,
        jwks_url=f"{auth_url}/.well-known/jwks.json" if auth_url else None,
    )
    return Authenticator(verifier, directory, settings.service_token)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Without DATABASE_URL (replay, CI's check job) the app still starts; DB routes cannot.
    engine = create_engine(settings) if settings.database_url and settings.db_schema else None
    storage = blob_store(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await storage.aclose()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Vitrina API", version=version("vitrina-api"), lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = create_session_factory(engine) if engine is not None else None
    app.state.jobs = PostgresJobQueue(app.state.sessions) if app.state.sessions else None
    app.state.storage = storage
    app.state.eval_runs = (
        PostgresEvalRuns(app.state.sessions) if app.state.sessions else InMemoryEvalRuns()
    )
    # GET /metrics/summary: each module's counters (vitrina.metrics); GET /health/providers.
    app.state.live_counters = load_counters()
    app.state.health_cache = HealthCache(settings.health_cache_s)
    identity = (
        ContactIdentity(pepper=settings.contact_hash_pepper, key=settings.contact_encryption_key)
        if settings.contact_hash_pepper and settings.contact_encryption_key
        else None
    )
    app.state.compliance = (
        PostgresCompliance(app.state.sessions, identity)
        if app.state.sessions
        else InMemoryCompliance(identity)
    )
    watch = SpendWatch(settings.spend_limit_usd)
    app.state.ledger = (
        PostgresLedger(app.state.sessions, watch) if app.state.sessions else InMemoryLedger(watch)
    )
    # Replay, the default, answers from cassettes and needs no key (AS-7.3, ADR-0010).
    app.state.providers = compose(settings, app.state.ledger, lambda: live_providers(settings))
    # No database: the in-memory catalog keeps every route usable on a laptop (replay, demos).
    app.state.catalog = (
        PostgresCatalog(
            app.state.sessions,
            embedder=app.state.providers.embedder,
            embed_model=settings.model_embed,
        )
        if app.state.sessions
        else InMemoryCatalog()
    )
    install_problem_handlers(app)
    for module in ROUTERS:
        app.include_router(module.router, prefix=API_PREFIX)
    if isinstance(storage, LocalBlobStore):
        # Signed links to local files; outside the contract and hidden from OpenAPI (I-15).
        app.include_router(create_blob_router(storage))
    if settings.whatsapp_mock:
        # Outside the contract and hidden from OpenAPI (plan.md §4.6, ADR-0011).
        app.include_router(create_phone_router(MockOutbox(settings.mock_outbox_path)))

    if app.state.sessions:
        app.state.sellers = PostgresSellerStore(app.state.sessions)
        directory: SellerDirectory = PostgresSellerDirectory(app.state.sessions)
    else:
        # No database: sellers created through POST /sellers sign in against the same store.
        app.state.sellers = directory = InMemorySellerStore()
    app.state.auth = authenticator(settings, directory)
    store: IdempotencyStore = (
        PostgresIdempotencyStore(app.state.sessions)
        if app.state.sessions
        else InMemoryIdempotencyStore()
    )
    app.add_middleware(IdempotencyMiddleware, store=store, scope=app.state.auth.idempotency_scope)
    # Outside idempotency, which buffers a POST body before authentication; inside request ids,
    # so a 413 carries one too.
    app.add_middleware(BodyLimitMiddleware, limits=BODY_LIMITS)
    # Added late, so outer: every response, errors included, carries X-Request-Id.
    app.add_middleware(RequestIdMiddleware)
    origins = [
        origin.strip() for origin in (settings.cors_origins or "").split(",") if origin.strip()
    ]
    if origins:  # outermost: preflights are answered before anything else runs
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
            expose_headers=["X-Request-Id"],
        )
    return app
