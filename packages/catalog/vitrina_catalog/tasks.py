"""Catalog jobs, discovered by the worker through the ``vitrina.tasks`` entry point."""

from vitrina_catalog.importer import IMPORT_TASK, extract_products, save_drafts
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_catalog.reservations import expire_reservations
from vitrina_catalog.sellers import PostgresSellerStore
from vitrina_catalog.voice import voice_profile
from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry
from vitrina_core.storage import BlobNotFoundError, seller_of

VOICE_PROFILE = "catalog.voice_profile"
EXPIRE_RESERVATIONS = "catalog.expire_reservations"
# Stock already ignores expired rows; the status only has to catch up (T052).
EXPIRE_EVERY_S = 600.0


async def build_voice_profile(ctx: JobContext) -> str:
    """AS-1.5: the payload holds the seller's redacted lines; customer lines never got here."""
    lines = list(ctx.payload.get("lines", []))
    if not lines or ctx.owner_seller_id is None or ctx.sessions is None:
        raise JobFailedError("VALIDATION_ERROR", "no seller lines to learn from")
    await ctx.progress("profiling", 0.5)
    await PostgresSellerStore(ctx.sessions).set_voice_profile(
        ctx.owner_seller_id, voice_profile(lines)
    )
    return "/v1/sellers/me"


async def import_products(ctx: JobContext) -> str:
    """AS-1.3: the photo the route stored becomes drafts the seller approves (FR-003)."""
    seller_id, key = ctx.owner_seller_id, str(ctx.payload.get("image_key", ""))
    if ctx.sessions is None or ctx.storage is None or ctx.providers is None:
        raise JobFailedError("INTERNAL_ERROR", "imports need the database, storage and providers")
    try:
        owner = seller_of(key)
    except ValueError:
        owner = None
    if seller_id is None or owner != seller_id:  # Art. V.3
        raise JobFailedError("VALIDATION_ERROR", "the photo does not belong to this seller")
    try:
        model = ctx.settings.model_id("agent")
    except LookupError as error:
        raise JobFailedError("PROVIDER_UNAVAILABLE", str(error)) from error
    try:
        photo = await ctx.storage.get(key)
    except BlobNotFoundError as error:
        raise JobFailedError("NOT_FOUND", "the photo is no longer stored") from error
    await ctx.progress("reading", 0.2)
    drafts = await extract_products(
        photo, ctx.payload.get("hint"), chat=ctx.providers.chat, model=model
    )
    await ctx.progress("saving", 0.8)
    catalog = PostgresCatalog(
        ctx.sessions, embedder=ctx.providers.embedder, embed_model=ctx.settings.model_embed
    )
    await save_drafts(catalog, seller_id, drafts)
    return "/v1/products?status=draft"


def register(registry: TaskRegistry) -> None:
    registry.add(VOICE_PROFILE, build_voice_profile, timeout_s=120)
    # One vision call, retried with backoff, and one re-ask on an unusable answer.
    registry.add(IMPORT_TASK, import_products, timeout_s=300)
    registry.every(EXPIRE_RESERVATIONS, every_s=EXPIRE_EVERY_S, sweep=expire_reservations)
