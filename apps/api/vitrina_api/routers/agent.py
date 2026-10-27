"""Agent router. Owner: MX2 (plan.md §3).

Contract paths: /agent/turns.
"""

from fastapi import APIRouter, Request

from vitrina_catalog.api import SellerSettings
from vitrina_conversation.api import (
    AgentDeps,
    AgentTurnRequest,
    AgentTurnResult,
    TurnRequest,
    agent_turn_result,
    run_turn,
)
from vitrina_core.auth import SellerId
from vitrina_core.http import IdempotencyKey, ProblemCode, ProblemError, problem_responses
from vitrina_core.ids import new_id
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import Settings

router = APIRouter(tags=["Agent"])


@router.post(
    "/agent/turns",
    operation_id="runAgentTurn",
    summary='Run one agent turn without WhatsApp (tests, evals, dashboard "try it")',
    description=(
        "Returns the structured output, tool calls and validator results (FR-030). "
        "With `dry_run=true` no tool has side effects."
    ),
    response_description="Agent turn result",
    responses=problem_responses(400, 503),
)
async def run_agent_turn(
    request: Request, seller_id: SellerId, body: AgentTurnRequest, _key: IdempotencyKey = None
) -> AgentTurnResult:
    if body.message.kind != "text":
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="image and audio turns arrive with T050 and T051; send kind=text",
        )
    text = body.message.text if isinstance(body.message.text, str) else ""
    if not text.strip():
        raise ProblemError(ProblemCode.VALIDATION_ERROR, detail="a text turn needs message.text")
    seller: SellerSettings | None = await request.app.state.sellers.settings(seller_id)
    if seller is None:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR, detail="this seller has no settings yet (POST /sellers)"
        )
    settings: Settings = request.app.state.settings
    try:
        model = settings.model_id("agent")
    except LookupError as error:
        raise ProblemError(ProblemCode.PROVIDER_UNAVAILABLE, detail=str(error)) from error
    chat = (
        compose(settings, request.app.state.ledger, mode=ProviderMode.replay).chat
        if body.replay_model_outputs
        else request.app.state.providers.chat
    )
    turn = TurnRequest(
        seller=seller,
        conversation_id=body.conversation_id or new_id(),
        message=text,
        dry_run=body.dry_run,
    )
    deps = AgentDeps(
        chat=chat,
        catalog=request.app.state.catalog,
        model=model,
        max_tool_rounds=settings.agent_max_tool_rounds,
        budget_s=settings.agent_turn_budget_s,
        top_k=settings.retrieval_top_k,
    )
    try:
        result = await run_turn(turn, deps)
    except TimeoutError as error:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail=f"the agent turn ran past its {settings.agent_turn_budget_s:g} s budget",
        ) from error
    return agent_turn_result(result)
