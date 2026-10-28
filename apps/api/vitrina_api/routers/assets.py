"""Assets router.

Contract paths: /assets, /assets/{asset_id} (T030, FR-010). Files go to the blob store (I-15);
answers carry a signed, short-lived link. The asset store hangs off ``app.state`` lazily
(``assets_from_state``), so ``create_app`` needs no creative wiring.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from vitrina_catalog.api import CatalogStore
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    FieldError,
    IdempotencyKey,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.storage import BlobStore
from vitrina_creative.api import (
    Asset,
    AssetStore,
    MediaRejectedError,
    asset_view,
    assets_from_state,
    upload_asset,
)
from vitrina_creative.media import VIDEO_MAX_BYTES

router = APIRouter(tags=["Assets"])

READ_CHUNK = 1024 * 1024


def asset_store(request: Request) -> AssetStore:
    return assets_from_state(request.app.state)


def blob_store(request: Request) -> BlobStore:
    store: BlobStore = request.app.state.storage
    return store


Store = Annotated[AssetStore, Depends(asset_store)]
Blobs = Annotated[BlobStore, Depends(blob_store)]


@router.post(
    "/assets",
    status_code=201,
    operation_id="uploadAsset",
    summary="Upload a product photo or short video",
    responses=problem_responses(400, 413),
)
async def upload(
    seller_id: SellerId,
    request: Request,
    store: Store,
    blobs: Blobs,
    file: Annotated[UploadFile, File()],
    product_id: Annotated[UUID, Form()],
    _key: IdempotencyKey = None,
) -> Asset:
    catalog: CatalogStore = request.app.state.catalog
    if await catalog.product(seller_id, product_id) is None:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="El producto no existe en tu catálogo.",
            errors=[FieldError(field="product_id", message="no existe en tu catálogo")],
        )
    data = await _read(file, VIDEO_MAX_BYTES)
    try:
        record = await upload_asset(
            seller_id=seller_id, product_id=product_id, data=data, store=store, blobs=blobs
        )
    except MediaRejectedError as error:
        if error.too_large:
            raise ProblemError(ProblemCode.PAYLOAD_TOO_LARGE, detail=error.message) from error
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail=error.message,
            errors=[FieldError(field="file", message=error.message)],
        ) from error
    return await asset_view(record, blobs)


@router.get(
    "/assets/{asset_id}",
    operation_id="getAsset",
    summary="Get asset metadata and signed URL",
    responses=problem_responses(404),
)
async def get_asset(seller_id: SellerId, store: Store, blobs: Blobs, asset_id: UUID) -> Asset:
    record = await store.get(seller_id, asset_id)
    if record is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no asset {asset_id} for this seller")
    return await asset_view(record, blobs)


async def _read(file: UploadFile, limit: int) -> bytes:
    """The upload, refused with 413 as soon as it passes the largest FR-010 limit."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(READ_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ProblemError(
                ProblemCode.PAYLOAD_TOO_LARGE,
                detail=f"El archivo pesa más de {limit // READ_CHUNK} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)
