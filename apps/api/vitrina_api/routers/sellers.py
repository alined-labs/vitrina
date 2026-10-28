"""Sellers router.

Contract paths: /sellers, /sellers/me, /sellers/me/voice-samples.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from vitrina_catalog.sellers import (
    Seller,
    SellerConflictError,
    SellerCreate,
    SellerStore,
    SellerUpdate,
)
from vitrina_catalog.tasks import VOICE_PROFILE
from vitrina_catalog.voice import redact, seller_lines
from vitrina_core.auth import SellerId, authenticator
from vitrina_core.http import (
    FieldError,
    IdempotencyKey,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.jobs import Job, PostgresJobQueue

router = APIRouter(tags=["Sellers"])

MAX_EXPORT_BYTES = 5 * 1024 * 1024  # openapi.yaml: WhatsApp export .txt ≤ 5 MB
MAX_LINES = 2000


def seller_store(request: Request) -> SellerStore:
    store: SellerStore = request.app.state.sellers
    return store


Store = Annotated[SellerStore, Depends(seller_store)]


async def _signed_in_subject(request: Request) -> str | None:
    """POST /sellers is open (security: []), but a dashboard user sends the Supabase token
    so the new seller is linked to the account that will sign in."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    claims = await authenticator(request).verifier.claims(token)
    return str(claims["sub"])


@router.post(
    "/sellers",
    status_code=201,
    operation_id="createSeller",
    summary="Create a seller account",
    responses=problem_responses(400, 409),
)
async def create_seller(
    request: Request, store: Store, seller: SellerCreate, _key: IdempotencyKey = None
) -> Seller:
    try:
        return await store.create(seller, auth_subject=await _signed_in_subject(request))
    except SellerConflictError as error:
        raise ProblemError(ProblemCode.CONFLICT, detail=str(error)) from error


async def _current(store: SellerStore, seller_id: UUID) -> Seller:
    seller = await store.get(seller_id)
    if seller is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail="this account's seller no longer exists")
    return seller


@router.get(
    "/sellers/me",
    operation_id="getCurrentSeller",
    summary="Get the authenticated seller",
    responses=problem_responses(401),
)
async def get_current_seller(seller_id: SellerId, store: Store) -> Seller:
    return await _current(store, seller_id)


@router.patch(
    "/sellers/me",
    operation_id="updateCurrentSeller",
    summary="Update seller settings (hours, thresholds, voice, AI label)",
    responses=problem_responses(400),
)
async def update_current_seller(seller_id: SellerId, store: Store, changes: SellerUpdate) -> Seller:
    seller = await store.update(seller_id, changes)
    if seller is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail="this account's seller no longer exists")
    return seller


@router.post(
    "/sellers/me/voice-samples",
    status_code=202,
    operation_id="importVoiceSamples",
    summary="Import an exported WhatsApp chat to learn the seller's voice",
    description=(
        "Only the seller's own lines are kept; customer lines are discarded and PII is redacted "
        "before storage (FR-004)."
    ),
    responses=problem_responses(400),
)
async def import_voice_samples(
    request: Request,
    seller_id: SellerId,
    file: Annotated[UploadFile, File(description="WhatsApp export .txt (≤ 5 MB)")],
    seller_display_name: Annotated[
        str, Form(description="Name as it appears in the export, used to keep only seller lines")
    ],
    _key: IdempotencyKey = None,
) -> Job:
    raw = await file.read(MAX_EXPORT_BYTES + 1)
    if len(raw) > MAX_EXPORT_BYTES:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            errors=[FieldError(field="file", message="the export is larger than 5 MB")],
        )
    lines = seller_lines(raw.decode("utf-8", errors="replace"), seller_display_name)
    if not lines:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            errors=[
                FieldError(
                    field="seller_display_name", message="no messages from this name in the export"
                )
            ],
        )
    jobs: PostgresJobQueue | None = getattr(request.app.state, "jobs", None)
    if jobs is None or request.app.state.sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail="voice import needs the database (DATABASE_URL)",
        )
    # Customer lines were dropped above; only redacted seller lines are stored (Art. V).
    payload = {"lines": [redact(line) for line in lines[-MAX_LINES:]]}
    async with request.app.state.sessions() as session, session.begin():
        job_id = await jobs.enqueue(
            session,
            kind="voice_import",
            task=VOICE_PROFILE,
            payload=payload,
            owner_seller_id=seller_id,
        )
    job = await jobs.get_for_seller(seller_id, job_id)
    assert job is not None
    return job
