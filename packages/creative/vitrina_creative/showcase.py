"""Foto → Vitrina, the public showcase (T083, P2; AS-8.2, FR-072).

Anyone can upload one product photo without an account, at most five times an hour from one
address. A job turns it into three scenes, one per format, and ten copy variants. Scenes that
pass verification are published at ``/v/{slug}`` with the AI label and the Vitrina watermark;
a blocked scene is listed without its image, so it never reaches a public page (FR-013). The
upload is deleted when the job ends, and the visitor's address is kept only as a keyed hash
that changes every hour. No seller is involved: files live under the nil seller id.
"""

import hashlib
import hmac
import re
import secrets
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.ids import NIL
from vitrina_core.jobs import JobContext, JobFailedError
from vitrina_core.storage import BlobNotFoundError, BlobStore, blob_key
from vitrina_creative.ads import fill_link
from vitrina_creative.compose import CreativeFormat
from vitrina_creative.pipeline import (
    PipelineInput,
    PipelineModels,
    ProductSnapshot,
    SellerSnapshot,
    run_pipeline,
)
from vitrina_creative.provenance import AI_LABEL, showcase_image
from vitrina_creative.schemas import Uri
from vitrina_creative.tables import public_rate_limits, public_showcases
from vitrina_creative.verifier import Thresholds

SHOWCASE_TASK = "creative.run_showcase"
RATE_LIMIT = 5
WINDOW = timedelta(hours=1)
FORMATS: tuple[CreativeFormat, ...] = ("story_9x16", "catalog_1x1", "feed_4x5")
COPIES = 10
OWNER = NIL
BUSINESS = "un negocio local"
Row = Mapping[Any, Any]


class ShowcaseCreated(BaseModel):
    job_id: UUID
    slug: str
    page_url: Uri


class ShowcaseScene(BaseModel):
    format: CreativeFormat | MISSING = MISSING
    image_url: Uri | MISSING = MISSING
    verification_status: Literal["passed", "blocked"] | MISSING = MISSING


class PublicShowcase(BaseModel):
    slug: str
    product_name: str
    status: Literal["processing", "ready", "failed"]
    scenes: list[ShowcaseScene]
    copies: list[str]
    # Required and defaulted in the contract: always sent, the default kept in the schema.
    ai_label: Annotated[str, Field(json_schema_extra={"default": AI_LABEL})]
    cta_url: Uri | MISSING = MISSING
    created_at: datetime | MISSING = MISSING


def new_slug(product_name: str) -> str:
    """``bolso-tejido-a-mano-3f9a1c``: readable, unguessable, within ``^[a-z0-9-]{6,40}$``."""
    folded = unicodedata.normalize("NFKD", product_name.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    words = re.findall(r"[a-z0-9]+", plain)
    stem = "-".join(words)[:26].strip("-") or "vitrina"
    return f"{stem}-{secrets.token_hex(3)}"


def window_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def address_hash(address: str, key: bytes, window: datetime) -> str:
    """A keyed hash of the visitor's address for one window: it cannot be reversed or linked."""
    message = f"{window.isoformat()}|{address}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


class ShowcaseStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def hit(self, digest: str, window: datetime) -> int:
        """Count one request in the address's window; returns the count so far."""
        insert = postgres_insert(public_rate_limits).values(
            ip_hash=digest, window_start=window, count=1
        )
        upsert = insert.on_conflict_do_update(
            index_elements=[public_rate_limits.c.ip_hash, public_rate_limits.c.window_start],
            set_={"count": public_rate_limits.c.count + 1},
        ).returning(public_rate_limits.c.count)
        async with self.sessions() as session, session.begin():
            count: int = (await session.execute(upsert)).scalar_one()
        return count

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        showcase_id: UUID,
        slug: str,
        product_name: str,
        price_cop: int | None,
        city: str | None,
        ip_hash: str,
    ) -> None:
        await session.execute(
            sa.insert(public_showcases).values(
                id=showcase_id,
                slug=slug,
                product_name=product_name,
                price_cop=price_cop,
                city=city,
                ip_hash=ip_hash,
                status="processing",
                scenes=[],
                copies=[],
            )
        )

    async def _one(self, condition: sa.ColumnElement[bool]) -> Row | None:
        async with self.sessions() as session:
            return (
                (await session.execute(sa.select(public_showcases).where(condition)))
                .mappings()
                .first()
            )

    async def by_slug(self, slug: str) -> Row | None:
        return await self._one(public_showcases.c.slug == slug)

    async def get(self, showcase_id: UUID) -> Row | None:
        return await self._one(public_showcases.c.id == showcase_id)

    async def finish(
        self, showcase_id: UUID, scenes: list[dict[str, str]], copies: list[str]
    ) -> None:
        await self._update(showcase_id, status="ready", scenes=scenes, copies=copies)

    async def fail(self, showcase_id: UUID) -> None:
        await self._update(showcase_id, status="failed")

    async def _update(self, showcase_id: UUID, **values: Any) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(public_showcases)
                .where(public_showcases.c.id == showcase_id)
                .values(**values)
            )


async def showcase_view(row: Row, blobs: BlobStore, *, web_url: str) -> PublicShowcase:
    scenes = []
    for entry in row["scenes"] or []:
        scene = ShowcaseScene(
            format=entry["format"], verification_status=entry["verification_status"]
        )
        if entry.get("key"):
            scene.image_url = await blobs.signed_url(entry["key"])
        scenes.append(scene)
    return PublicShowcase(
        slug=row["slug"],
        product_name=row["product_name"],
        status=row["status"],
        scenes=scenes,
        copies=list(row["copies"] or []),
        ai_label=AI_LABEL,
        cta_url=f"{web_url.rstrip('/')}/login",
        created_at=row["created_at"],
    )


async def run_showcase(ctx: JobContext) -> str:
    """The job behind POST /public/showcases: scenes, copy, the public files, then cleanup."""
    if ctx.sessions is None or ctx.storage is None or ctx.providers is None:
        raise JobFailedError("INTERNAL_ERROR", "showcases need the database, storage and providers")
    store = ShowcaseStore(ctx.sessions)
    showcase_id = UUID(str(ctx.payload["showcase_id"]))
    source_key = str(ctx.payload["source_key"])
    row = await store.get(showcase_id)
    if row is None:
        raise JobFailedError("NOT_FOUND", "the showcase is gone")
    result_ref = f"/v1/public/showcases/{row['slug']}"
    if row["status"] != "processing":
        return result_ref
    try:
        photo = await ctx.storage.get(source_key)
        models = PipelineModels(
            segment=ctx.settings.model_id("segment"),
            image=ctx.settings.model_id("image"),
            chat=ctx.settings.model_id("agent"),
        )
    except BlobNotFoundError as error:
        await store.fail(showcase_id)
        raise JobFailedError("NOT_FOUND", "La foto ya no está guardada.") from error
    except LookupError as error:
        await store.fail(showcase_id)
        await _forget(ctx.storage, source_key)
        raise JobFailedError("PROVIDER_UNAVAILABLE", str(error)) from error

    async def on_stage(stage: str, fraction: float) -> None:
        await ctx.progress(stage, fraction)

    data = PipelineInput(
        product=ProductSnapshot(name=row["product_name"], price_cop=row["price_cop"]),
        seller=SellerSnapshot(business_name=BUSINESS, city=row["city"] or "Colombia"),
        photos=((showcase_id, photo),),
        formats=FORMATS,
        scenes_per_format=1,
        copy_variants=COPIES,
    )
    try:
        result = await run_pipeline(
            data,
            segmenter=ctx.providers.segmenter,
            image_model=ctx.providers.image,
            chat=ctx.providers.chat,
            models=models,
            thresholds=Thresholds.from_settings(ctx.settings),
            on_stage=on_stage,
        )
    except JobFailedError:
        await store.fail(showcase_id)
        await _forget(ctx.storage, source_key)
        raise
    except Exception:
        if ctx.job.attempt >= ctx.job.max_attempts:  # the worker gives up after this attempt
            await store.fail(showcase_id)
            await _forget(ctx.storage, source_key)
        raise

    scenes: list[dict[str, str]] = []
    for index, scene in enumerate(result.scenes):
        passed = scene.verification.status == "passed"
        entry = {"format": scene.format, "verification_status": "passed" if passed else "blocked"}
        if passed:
            key = blob_key(OWNER, "showcases", f"{row['slug']}/{index:02d}-{scene.format}", "png")
            await ctx.storage.put(key, showcase_image(scene.image).data, "image/png")
            entry["key"] = key
        scenes.append(entry)
    copies = [
        fill_link(variant.text, None) for variant in result.copy if variant.status == "candidate"
    ]
    await store.finish(showcase_id, scenes, copies)
    await _forget(ctx.storage, source_key)
    return result_ref


async def _forget(storage: BlobStore, key: str) -> None:
    """The visitor's upload is never kept once the job is over."""
    try:
        await storage.delete(key)
    except BlobNotFoundError:
        return
