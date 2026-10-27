"""Creative router. Owner: MX1 (plan.md §3).

Contract paths: /creative/jobs, /creative/jobs/{job_id}, /creatives/{creative_id} (T034);
/creative/reels arrives with T083 (P2). Starting a job is ``creative.api.start_job``, which
``learning.apply_brief`` also calls (I-13); the worker runs ``creative.run_job``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from vitrina_core.auth import SellerId
from vitrina_core.http import (
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.jobs import PostgresJobQueue
from vitrina_creative.api import (
    Creative,
    CreativeJob,
    CreativeJobCreate,
    CreativeJobStore,
    assets_from_state,
    creative_view,
    job_view,
    start_job,
)

router = APIRouter(tags=["Creative"])


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
    job_id = await start_job(
        seller_id,
        body,
        catalog=state.catalog,
        sellers=state.sellers,
        assets=assets_from_state(state),
        queue=queue,
        sessions=state.sessions,
    )
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
