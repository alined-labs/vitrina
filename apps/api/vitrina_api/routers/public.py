"""Public router.

Contract paths: /public/showcases, /public/showcases/{slug} (T083, P2; AS-8.2, FR-072). No sign-in:
the visitor's address, hashed with a key and the hour, limits uploads to five an hour.
Behind the dashboard's proxy the address arrives in ``X-Vitrina-Visitor`` (the proxy drops the
forwarding headers and always sets this one); a direct call is limited by its own address.
After the limit counts the attempt, Spark answers one yes/no question about the photo; a
refused photo is answered in Spanish and never stored, and no job is queued.
"""

import ipaddress
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, Request, UploadFile

from vitrina_core.http import FieldError, ProblemCode, ProblemError, problem_responses
from vitrina_core.ids import new_id
from vitrina_core.jobs import PostgresJobQueue
from vitrina_core.providers import Blob, ProviderError
from vitrina_core.settings import Settings
from vitrina_core.storage import BlobStore, blob_key
from vitrina_creative.api import (
    RATE_LIMIT,
    SHOWCASE_TASK,
    UPLOAD_REFUSED,
    UPLOAD_UNCHECKED,
    WINDOW,
    MediaRejectedError,
    PublicShowcase,
    ShowcaseCreated,
    ShowcaseStore,
    address_hash,
    new_slug,
    showcase_view,
    upload_allowed,
    window_start,
)
from vitrina_creative.media import IMAGE_MAX_BYTES, inspect_media
from vitrina_creative.showcase import OWNER

router = APIRouter(tags=["Public"])

READ_CHUNK = 1024 * 1024
# Without CONTACT_HASH_PEPPER (local runs) the key lives as long as the process.
_PROCESS_KEY = secrets.token_bytes(32)


def web_url(settings: Settings) -> str:
    """Where the dashboard serves /v/{slug}; PUBLIC_WEB_URL once core settings carry it."""
    return str(getattr(settings, "public_web_url", None) or "http://localhost:3000")


def _key(settings: Settings) -> bytes:
    pepper = settings.contact_hash_pepper
    return pepper.get_secret_value().encode() if pepper is not None else _PROCESS_KEY


def _store(request: Request) -> tuple[ShowcaseStore, PostgresJobQueue]:
    state = request.app.state
    if state.sessions is None or state.jobs is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE, detail="showcases need the database (DATABASE_URL)"
        )
    return ShowcaseStore(state.sessions), state.jobs


def visitor_address(request: Request, settings: Settings) -> str:
    """The address the showcase's hourly limit counts (T083, AS-8.2).

    The dashboard's proxy names the visitor in X-Vitrina-Visitor and proves it is the proxy with
    X-Vitrina-Proxy-Secret (VISITOR_PROXY_SECRET). Any other caller is counted by its own
    address, which uvicorn takes from the ingress (deploy/README.md).
    """
    if settings.visitor_proxy_matches(request.headers.get("x-vitrina-proxy-secret")):
        forwarded = request.headers.get("x-vitrina-visitor", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


@router.post(
    "/public/showcases",
    status_code=202,
    operation_id="createPublicShowcase",
    summary='Viral tool "Foto → Vitrina": one photo in, 3 watermarked scenes + 10 copies out (P2)',
    response_description="Job plus the slug the page will live at",
    responses=problem_responses(429),
)
async def create_showcase(
    request: Request,
    image: Annotated[UploadFile, File()],
    product_name: Annotated[str, Form(max_length=80)],
    price_cop: Annotated[int | None, Form(ge=0)] = None,
    city: Annotated[str | None, Form(max_length=60)] = None,
) -> ShowcaseCreated:
    store, queue = _store(request)
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    window = window_start(now)
    digest = address_hash(visitor_address(request, settings), _key(settings), window)
    if await store.hit(digest, window) > RATE_LIMIT:
        retry = int((window + WINDOW - now).total_seconds()) + 1
        raise ProblemError(
            ProblemCode.RATE_LIMITED,
            detail="Ya creaste 5 vitrinas en esta hora. Vuelve a intentarlo más tarde.",
            headers={"Retry-After": str(retry)},
        )
    data = await _read(image)
    try:
        media = inspect_media(data)
    except MediaRejectedError as error:
        raise _invalid_image(error.message) from error
    if media.kind != "image":
        raise _invalid_image("Sube una foto JPEG, PNG o WebP.")
    try:
        allowed = await upload_allowed(
            Blob(data=data, mime_type=media.mime_type),
            chat=request.app.state.providers.chat,
            model=settings.model_id("agent"),
        )
    except (ProviderError, LookupError) as error:
        raise ProblemError(ProblemCode.PROVIDER_UNAVAILABLE, detail=UPLOAD_UNCHECKED) from error
    if not allowed:
        raise _invalid_image(UPLOAD_REFUSED)

    showcase_id, slug = new_id(), new_slug(product_name)
    source_key = blob_key(OWNER, "showcases", f"{slug}/source", media.extension)
    blobs: BlobStore = request.app.state.storage
    await blobs.put(source_key, data, media.mime_type)
    async with request.app.state.sessions() as session, session.begin():
        await ShowcaseStore.create(
            session,
            showcase_id=showcase_id,
            slug=slug,
            product_name=product_name.strip(),
            price_cop=price_cop,
            city=city.strip() if city else None,
            ip_hash=digest,
        )
        job_id = await queue.enqueue(
            session,
            kind="public_showcase",
            task=SHOWCASE_TASK,
            payload={"showcase_id": str(showcase_id), "source_key": source_key},
        )
    return ShowcaseCreated(
        job_id=job_id, slug=slug, page_url=f"{web_url(settings).rstrip('/')}/v/{slug}"
    )


@router.get(
    "/public/showcases/{slug}",
    operation_id="getPublicShowcase",
    summary="Public showcase page data",
    response_description="Showcase",
    responses=problem_responses(404),
)
async def get_showcase(
    request: Request, slug: Annotated[str, Path(pattern=r"^[a-z0-9-]{6,40}$")]
) -> PublicShowcase:
    store, _queue = _store(request)
    row = await store.by_slug(slug)
    if row is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no showcase {slug}")
    settings: Settings = request.app.state.settings
    return await showcase_view(row, request.app.state.storage, web_url=web_url(settings))


def _invalid_image(message: str) -> ProblemError:
    return ProblemError(
        ProblemCode.VALIDATION_ERROR,
        detail=message,
        errors=[FieldError(field="image", message=message)],
    )


async def _read(file: UploadFile) -> bytes:
    """The photo, refused with 413 as soon as it passes 10 MB (FR-010)."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(READ_CHUNK):
        total += len(chunk)
        if total > IMAGE_MAX_BYTES:
            raise ProblemError(ProblemCode.PAYLOAD_TOO_LARGE, detail="La foto pesa más de 10 MB.")
        chunks.append(chunk)
    return b"".join(chunks)
