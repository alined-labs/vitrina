"""Creative router. Owner: MX1 (plan.md §3).

Contract paths: /creative/jobs, /creative/jobs/{job_id}, /creatives/{creative_id} (T034);
/creative/reels arrives with T083 (P2). Starting a job snapshots what the pipeline needs (product,
active facts, seller voice, photo keys) through the catalog's public interface, then queues the job
and its creative row in one transaction; the worker runs ``creative.run_job``.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from vitrina_catalog.api import CatalogStore, Product, SellerStore
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    FieldError,
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.jobs import PostgresJobQueue
from vitrina_creative.api import (
    CREATIVE_TASK,
    Creative,
    CreativeJob,
    CreativeJobCreate,
    CreativeJobStore,
    assets_from_state,
    creative_view,
    job_view,
)

router = APIRouter(tags=["Creative"])

MAX_FACTS = 50
VOICE_KEYS = ("formality", "warmth", "emoji_rate", "regionalisms")


def job_store(request: Request) -> CreativeJobStore:
    sessions = request.app.state.sessions
    if sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail="creative jobs need the database (DATABASE_URL)",
        )
    return CreativeJobStore(sessions)


Jobs = Annotated[CreativeJobStore, Depends(job_store)]
Paging = Annotated[PageQuery, Depends()]


@router.post(
    "/creative/jobs",
    status_code=202,
    operation_id="createCreativeJob",
    summary="Run the creative pipeline for a product (segment → compose → verify → copy)",
    responses=problem_responses(400, 404),
)
async def create_job(
    seller_id: SellerId,
    request: Request,
    store: Jobs,
    body: CreativeJobCreate,
    _key: IdempotencyKey = None,
) -> CreativeJob:
    state = request.app.state
    queue: PostgresJobQueue | None = state.jobs
    if queue is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE, detail="creative jobs need the database"
        )
    catalog: CatalogStore = state.catalog
    product = await catalog.product(seller_id, body.product_id)
    if product is None:
        raise ProblemError(
            ProblemCode.NOT_FOUND, detail=f"no product {body.product_id} for this seller"
        )
    payload = {
        "params": body.model_dump(mode="json"),
        "product": _product(product),
        "facts": await _facts(catalog, seller_id),
        "seller": await _seller(state.sellers, seller_id),
        "photos": await _photos(state, seller_id, body),
    }
    async with state.sessions() as session, session.begin():
        job_id = await queue.enqueue(
            session, kind="creative", task=CREATIVE_TASK, payload=payload, owner_seller_id=seller_id
        )
        await CreativeJobStore.create(session, job_id=job_id, seller_id=seller_id, params=body)
    row = await store.get(seller_id, job_id)
    if row is None:  # pragma: no cover - the row was inserted in the same transaction
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no creative job {job_id}")
    return await job_view(row)


@router.get("/creative/jobs", operation_id="listCreativeJobs", summary="List creative jobs")
async def list_jobs(
    seller_id: SellerId, store: Jobs, page: Paging, product_id: UUID | None = None
) -> Page[CreativeJob]:
    rows, cursor = await store.page(
        seller_id, limit=page.limit, cursor=page.cursor, product_id=product_id
    )
    return Page[CreativeJob](items=[await job_view(row) for row in rows], next_cursor=cursor)


@router.get(
    "/creative/jobs/{job_id}",
    operation_id="getCreativeJob",
    summary="Get a creative job with its creatives and copy variants",
    responses=problem_responses(404),
)
async def get_job(seller_id: SellerId, request: Request, store: Jobs, job_id: UUID) -> CreativeJob:
    row = await store.get(seller_id, job_id)
    if row is None:
        raise ProblemError(
            ProblemCode.NOT_FOUND, detail=f"no creative job {job_id} for this seller"
        )
    return await job_view(
        row,
        creative_rows=await store.creatives_for(seller_id, job_id),
        copy_rows=await store.copy_for(seller_id, job_id),
        blobs=request.app.state.storage,
    )


@router.get(
    "/creatives/{creative_id}",
    operation_id="getCreative",
    summary="Get a creative with its verification report",
    responses=problem_responses(404),
)
async def get_creative(
    seller_id: SellerId, request: Request, store: Jobs, creative_id: UUID
) -> Creative:
    row = await store.creative(seller_id, creative_id)
    if row is None:
        raise ProblemError(
            ProblemCode.NOT_FOUND, detail=f"no creative {creative_id} for this seller"
        )
    return await creative_view(row, request.app.state.storage)


def _product(product: Product) -> dict[str, Any]:
    data = product.model_dump(mode="json")
    keys = ("name", "description", "price_cop", "stock", "attributes")
    return {key: data[key] for key in keys if key in data}


async def _facts(catalog: CatalogStore, seller_id: UUID) -> list[str]:
    facts, _cursor = await catalog.list_facts(seller_id, limit=MAX_FACTS)
    return [fact.text for fact in facts if fact.status == "active"]


async def _seller(sellers: SellerStore, seller_id: UUID) -> dict[str, Any]:
    seller = await sellers.get(seller_id)
    settings = await sellers.settings(seller_id)
    if seller is None or settings is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail="sign up with POST /sellers first")
    profile = settings.brand_voice_profile.model_dump(mode="json")
    return {
        "business_name": seller.business_name,
        "city": seller.city,
        "voice": {key: profile[key] for key in VOICE_KEYS if key in profile},
        "brand_voice_min": settings.brand_voice_min,
        "ai_label_enabled": settings.ai_label_enabled,
    }


async def _photos(state: Any, seller_id: UUID, body: CreativeJobCreate) -> list[dict[str, str]]:
    images = [
        record
        for record in await assets_from_state(state).for_product(seller_id, body.product_id)
        if record.kind == "image"
    ]
    if isinstance(body.asset_ids, list):
        wanted = set(body.asset_ids)
        images = [record for record in images if record.id in wanted]
        if len(images) != len(wanted):
            raise ProblemError(
                ProblemCode.VALIDATION_ERROR,
                errors=[
                    FieldError(
                        field="asset_ids", message="incluye fotos que no son de este producto"
                    )
                ],
            )
    if not images:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="Sube al menos una foto del producto.",
            errors=[FieldError(field="asset_ids", message="el producto no tiene fotos")],
        )
    return [{"asset_id": str(record.id), "key": record.storage_key} for record in images]
