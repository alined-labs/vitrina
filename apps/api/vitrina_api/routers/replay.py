"""Replay router (T080).

Contract paths: /replay/fixtures, /replay/sessions. A session is a ``replay`` job that plays a
recorded conversation through the normal pipeline with ``is_replay`` and the local mock
(FR-071), so it needs the database, as eval runs do.
"""

from fastapi import APIRouter, Request

from vitrina_conversation.api import conversation_store
from vitrina_conversation.replay import (
    LiveConversationError,
    replay_fixture,
    replay_fixtures,
    start_replay_session,
)
from vitrina_conversation.schemas import (
    ReplayFixtureView,
    ReplaySessionCreate,
    ReplaySessionStarted,
)
from vitrina_core.auth import SellerId
from vitrina_core.http import IdempotencyKey, ProblemCode, ProblemError

router = APIRouter(tags=["Replay"])


@router.get(
    "/replay/fixtures",
    operation_id="listReplayFixtures",
    summary="Recorded webhook fixtures available for replay",
    response_description="Fixtures",
)
async def list_replay_fixtures(_seller: SellerId) -> list[ReplayFixtureView]:
    return [
        ReplayFixtureView(
            fixture_id=fixture.fixture_id,
            name=fixture.name,
            description=fixture.description,
            message_count=fixture.message_count,
        )
        for fixture in replay_fixtures()
    ]


@router.post(
    "/replay/sessions",
    status_code=202,
    operation_id="startReplaySession",
    summary="Inject a recorded conversation through the live pipeline (no real sends)",
    response_description="Session started",
)
async def start_replay(
    request: Request, seller_id: SellerId, body: ReplaySessionCreate, _key: IdempotencyKey = None
) -> ReplaySessionStarted:
    fixture = replay_fixture(body.fixture_id)
    if fixture is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"no replay fixture {body.fixture_id!r}")
    state = request.app.state
    if state.sessions is None or state.jobs is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail="replay sessions are jobs and need the database (DATABASE_URL)",
        )
    try:
        session = await start_replay_session(
            sessions=state.sessions,
            store=conversation_store(state),
            compliance=state.compliance,
            seller_id=seller_id,
            fixture=fixture,
            speed=body.speed,
            use_recorded_model_outputs=body.use_recorded_model_outputs,
        )
    except LiveConversationError as error:
        raise ProblemError(ProblemCode.CONFLICT, detail=str(error)) from error
    return ReplaySessionStarted(
        session_id=session.session_id,
        conversation_id=session.conversation_id,
        job_id=session.job_id,
    )
