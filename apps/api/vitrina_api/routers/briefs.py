"""Briefs router. Owner: MX0 (plan.md §3).

Contract paths: /briefs, /briefs/generate, /briefs/{brief_id}, /briefs/{brief_id}/apply.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

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
from vitrina_core.jobs import Job, PostgresJobQueue
from vitrina_learning.api import GENERATE_TASK, Brief, PostgresBriefs, attributed_chats

router = APIRouter(tags=["Briefs"])

Paging = Annotated[PageQuery, Depends()]


class BriefGenerate(BaseModel):
    period_from: date
    period_to: date
    min_conversations: int = Field(default=10, ge=1)


@router.get("/briefs", operation_id="listBriefs", summary="List weekly briefs")
async def list_briefs(seller_id: SellerId, request: Request, page: Paging) -> Page[Brief]:
    sessions = request.app.state.sessions
    if sessions is None:  # in-memory mode writes no briefs
        return Page[Brief](items=[], next_cursor=None)
    items, cursor = await PostgresBriefs(sessions).page(
        seller_id, limit=page.limit, cursor=page.cursor
    )
    return Page[Brief](items=items, next_cursor=cursor)


@router.post(
    "/briefs/generate",
    status_code=202,
    operation_id="generateBrief",
    summary="Ask Muse Wonder for the next creative brief from objections and attribution",
    response_description="Accepted; poll the job",
    responses=problem_responses(422),
)
async def generate_brief(
    request: Request, seller_id: SellerId, body: BriefGenerate, _key: IdempotencyKey = None
) -> Job:
    if body.period_from > body.period_to:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="the period ends before it starts",
            errors=[FieldError(field="period_to", message="must be on or after period_from")],
        )
    jobs: PostgresJobQueue | None = request.app.state.jobs
    sessions = request.app.state.sessions
    if jobs is None or sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail="briefs are jobs and need the database (DATABASE_URL)",
        )
    chats = await attributed_chats(sessions, seller_id, body.period_from, body.period_to)
    if chats < body.min_conversations:  # AS-5.1
        raise ProblemError(
            ProblemCode.INSUFFICIENT_DATA,
            detail=(
                f"{chats} conversaciones atribuidas a anuncios en el periodo; el brief necesita"
                f" al menos {body.min_conversations}"
            ),
        )
    async with sessions() as session, session.begin():
        job_id = await jobs.enqueue(
            session,
            kind="brief",
            task=GENERATE_TASK,
            payload={
                "period_from": body.period_from.isoformat(),
                "period_to": body.period_to.isoformat(),
            },
            owner_seller_id=seller_id,
        )
    job = await jobs.get_for_seller(seller_id, job_id)
    assert job is not None
    return job


@router.get(
    "/briefs/{brief_id}",
    operation_id="getBrief",
    summary="Get a brief",
    responses=problem_responses(404),
)
async def get_brief(seller_id: SellerId, request: Request, brief_id: UUID) -> Brief:
    sessions = request.app.state.sessions
    brief = await PostgresBriefs(sessions).get(seller_id, brief_id) if sessions else None
    if brief is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no brief {brief_id}")
    return brief
