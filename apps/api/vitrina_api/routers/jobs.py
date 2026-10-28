"""Jobs router.

Contract paths: /jobs/{job_id}.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from vitrina_core.auth import SellerId
from vitrina_core.http import ProblemCode, ProblemError, problem_responses
from vitrina_core.jobs import Job, JobReader

router = APIRouter(tags=["Jobs"])


def job_reader(request: Request) -> JobReader:
    reader: JobReader | None = getattr(request.app.state, "jobs", None)
    if reader is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE, detail="jobs need the database (DATABASE_URL)"
        )
    return reader


@router.get(
    "/jobs/{job_id}",
    operation_id="getJob",
    summary="Poll any asynchronous job",
    responses=problem_responses(404),
)
async def get_job(
    job_id: UUID, seller_id: SellerId, jobs: Annotated[JobReader, Depends(job_reader)]
) -> Job:
    job = await jobs.get_for_seller(seller_id, job_id)
    if job is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no job {job_id} for this seller")
    return job
