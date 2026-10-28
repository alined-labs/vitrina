"""Ads router. Owner: MX1 (plan.md §3).

Contract paths: /ads, /ads/{ad_id}, /ads/{ad_id}/approve, /ads/{ad_id}/export, /ads/by-code/{code}
(T042; AS-2.3, AS-2.5; FR-013..015). Ads live in the database with their creatives, so every
operation answers 503 without one. Reads carry ``stats`` from learning's attribution (T044, I-10):
lifetime chats, reservations and bookings, present only for ads that had any.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from vitrina_catalog.api import SellerStore
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.storage import BlobStore
from vitrina_creative.api import CODE_PATTERN, Ad, AdCreate, AdStatus, AdStore, export_package
from vitrina_learning.api import ad_stats

router = APIRouter(tags=["Ads"])


def ad_store(request: Request) -> AdStore:
    sessions = request.app.state.sessions
    if sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE, detail="ads need the database (DATABASE_URL)"
        )
    return AdStore(sessions)


Ads = Annotated[AdStore, Depends(ad_store)]
Paging = Annotated[PageQuery, Depends()]


def _not_found(ad_id: UUID | str) -> ProblemError:
    return ProblemError(ProblemCode.NOT_FOUND, detail=f"no ad {ad_id} for this seller")


async def _with_stats(request: Request, seller_id: UUID, ads: list[Ad]) -> list[Ad]:
    """The ads with ``stats`` filled where learning counted activity (seller-scoped)."""
    found = await ad_stats(request.app.state.sessions, seller_id, [ad.id for ad in ads])
    return [ad.model_copy(update={"stats": found[ad.id]}) if ad.id in found else ad for ad in ads]


@router.get(
    "/ads",
    operation_id="listAds",
    summary="List ads (blocked creatives never appear)",
    response_description="Page of ads",
)
async def list_ads(
    request: Request,
    seller_id: SellerId,
    store: Ads,
    page: Paging,
    status: AdStatus | None = None,
    product_id: UUID | None = None,
) -> Page[Ad]:
    items, cursor = await store.page(
        seller_id, limit=page.limit, cursor=page.cursor, status=status, product_id=product_id
    )
    return Page[Ad](items=await _with_stats(request, seller_id, list(items)), next_cursor=cursor)


@router.post(
    "/ads",
    status_code=201,
    operation_id="createAd",
    summary="Create a draft ad from a passed creative and a copy variant",
    response_description="Draft ad",
    responses={
        409: {
            "description": "Creative is blocked by the fidelity verifier (`CREATIVE_BLOCKED`)",
            **problem_responses(409)[409],
        }
    },
)
async def create_ad(
    seller_id: SellerId, store: Ads, body: AdCreate, _key: IdempotencyKey = None
) -> Ad:
    return await store.create(seller_id, body)


@router.get(
    "/ads/by-code/{code}",
    operation_id="getAdByCode",
    summary="Resolve a short code like `A7`",
    response_description="Ad",
    responses=problem_responses(404),
)
async def get_ad_by_code(
    request: Request,
    seller_id: SellerId,
    store: Ads,
    code: Annotated[str, Path(pattern=CODE_PATTERN)],
) -> Ad:
    ad = await store.by_code(seller_id, code)
    if ad is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no ad #{code} for this seller")
    [ad] = await _with_stats(request, seller_id, [ad])
    return ad


@router.get(
    "/ads/{ad_id}",
    operation_id="getAd",
    summary="Get an ad",
    response_description="Ad",
    responses=problem_responses(404),
)
async def get_ad(request: Request, seller_id: SellerId, store: Ads, ad_id: UUID) -> Ad:
    ad = await store.get(seller_id, ad_id)
    if ad is None:
        raise _not_found(ad_id)
    [ad] = await _with_stats(request, seller_id, [ad])
    return ad


@router.delete(
    "/ads/{ad_id}",
    status_code=204,
    operation_id="archiveAd",
    summary="Archive an ad (its code stays reserved for attribution)",
    response_description="Archived",
)
async def archive_ad(seller_id: SellerId, store: Ads, ad_id: UUID) -> Response:
    if not await store.archive(seller_id, ad_id):
        raise _not_found(ad_id)
    return Response(status_code=204)


@router.post(
    "/ads/{ad_id}/approve",
    operation_id="approveAd",
    summary="Approve an ad; assigns the short code and wa.me link",
    response_description="Approved ad with `code` and `wa_link`",
    responses=problem_responses(409),
)
async def approve_ad(
    seller_id: SellerId, store: Ads, ad_id: UUID, _key: IdempotencyKey = None
) -> Ad:
    return await store.approve(seller_id, ad_id)


@router.get(
    "/ads/{ad_id}/export",
    operation_id="exportAd",
    summary="Download the ad package (images per format, copy, provenance.json)",
    response_class=Response,
    responses={
        200: {
            "description": "Zip archive",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        },
        **problem_responses(404),
    },
)
async def export_ad(seller_id: SellerId, request: Request, store: Ads, ad_id: UUID) -> Response:
    found = await store.parts(seller_id, ad_id)
    if found is None:
        raise _not_found(ad_id)
    ad, details = found
    if ad.code is None:
        raise ProblemError(ProblemCode.CONFLICT, detail="Aprueba el anuncio antes de descargarlo.")
    storage: BlobStore = request.app.state.storage
    sellers: SellerStore = request.app.state.sellers
    settings = await sellers.settings(seller_id)
    label = settings.ai_label_enabled if settings is not None else True
    image = await storage.get(details["storage_key"])
    body = export_package(ad, details, image, label=label)
    return Response(
        body,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="anuncio-{ad.code}.zip"'},
    )
