"""Catalog jobs, discovered by the worker through the ``vitrina.tasks`` entry point."""

from vitrina_catalog.sellers import PostgresSellerStore
from vitrina_catalog.voice import voice_profile
from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry

VOICE_PROFILE = "catalog.voice_profile"


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


def register(registry: TaskRegistry) -> None:
    registry.add(VOICE_PROFILE, build_voice_profile, timeout_s=120)
