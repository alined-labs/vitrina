"""openapi.yaml schemas of ``POST /agent/turns`` (FR-030) and the mapping from a ``TurnResult``.

The response's ``AgentTurnOutput`` is the API shape: three required fields and nullable as
a whole, null when no valid answer came back. The model's own answer is held to the strict
agent-tools.json shape (``contract.AgentTurnOutput``); research/OPEN.md Q15 and C2.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

from vitrina_conversation.contract import HandoffReason, ObjectionTag
from vitrina_conversation.engine import TurnResult


class AgentTurnMessage(BaseModel):
    kind: Literal["text", "image", "audio"]
    text: str | MISSING = MISSING
    media_url: Annotated[str, Field(json_schema_extra={"format": "uri"})] | MISSING = MISSING


class AgentTurnRequest(BaseModel):
    conversation_id: UUID | None = None
    message: AgentTurnMessage
    dry_run: bool = True
    replay_model_outputs: bool = False


class AgentTurnOutput(BaseModel):
    reply: Annotated[str, Field(max_length=1200)]
    cited_fact_ids: list[UUID]
    confidence: Annotated[float, Field(ge=0, le=1)]
    handoff_reason: HandoffReason | None = None
    objection_tag: ObjectionTag | None = None
    proposed_consent_prompt: bool = False


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | MISSING = MISSING
    status: Literal["ok", "error", "skipped_dry_run"]
    latency_ms: int | MISSING = MISSING


class PriceValidation(BaseModel):
    passed: bool
    amounts_in_reply: list[int] | MISSING = MISSING
    amounts_in_citations: list[int] | MISSING = MISSING
    violations: list[int] | MISSING = MISSING
    retried: bool | MISSING = MISSING


class GroundednessValidation(BaseModel):
    passed: bool
    uncited_claims: list[str] | MISSING = MISSING


class ScopeValidation(BaseModel):
    passed: bool
    reason: str | MISSING = MISSING


class ValidationResult(BaseModel):
    price_validator: PriceValidation
    groundedness: GroundednessValidation
    scope: ScopeValidation


class RetrievedItem(BaseModel):
    id: UUID | MISSING = MISSING
    kind: Literal["fact", "product"] | MISSING = MISSING
    score: float | MISSING = MISSING


class ModelCallMeta(BaseModel):
    """openapi.yaml ``ModelCallMeta`` (FR-084); every property optional."""

    provider: Literal["meta_model_api", "modal", "baseten", "fireworks", "local_mock"] | MISSING = (
        MISSING
    )
    model: str | MISSING = MISSING
    tokens_in: int | MISSING = MISSING
    tokens_out: int | MISSING = MISSING
    latency_ms: int | MISSING = MISSING
    cost_usd: float | MISSING = MISSING
    replayed: bool | MISSING = MISSING


class AgentTurnResult(BaseModel):
    conversation_id: UUID
    output: AgentTurnOutput | None
    tool_calls: list[ToolCallRecord]
    validation: ValidationResult
    final_reply: str
    retrieved: list[RetrievedItem] | MISSING = MISSING
    model_calls: list[ModelCallMeta]


NO_ANSWER = "no valid AgentTurnOutput after one repair call; counted as confidence 0 (R3, R4)"


def agent_turn_result(result: TurnResult) -> AgentTurnResult:
    """The API view of one turn: what the model said, what ran, what the checks found."""
    output = result.output
    review = result.review
    if review is not None:
        price = PriceValidation(
            passed=review.price.passed,
            amounts_in_reply=list(review.price.amounts_in_reply),
            amounts_in_citations=list(review.price.amounts_in_citations),
            violations=list(review.price.violations),
            retried=review.retried,
        )
        groundedness = GroundednessValidation(
            passed=review.groundedness.passed,
            uncited_claims=list(review.groundedness.uncited_claims),
        )
    else:
        price = PriceValidation(passed=True, amounts_in_reply=[], violations=[], retried=False)
        groundedness = GroundednessValidation(passed=False, uncited_claims=[NO_ANSWER])
    # The scope classifier joins with T054; until then the model's own off-topic tag decides.
    scope = ScopeValidation(
        passed=not result.off_topic,
        reason="off_topic: declined by the agent" if result.off_topic else "on_topic",
    )
    return AgentTurnResult(
        conversation_id=result.conversation_id,
        output=(
            AgentTurnOutput(
                reply=output.reply,
                cited_fact_ids=list(output.cited_fact_ids),
                confidence=output.confidence,
                handoff_reason=output.handoff_reason,
                objection_tag=output.objection_tag,
                proposed_consent_prompt=output.proposed_consent_prompt,
            )
            if output is not None
            else None
        ),
        tool_calls=[
            ToolCallRecord(
                name=record.name,
                arguments=record.arguments,
                result=record.result,
                status=record.status,
                latency_ms=record.latency_ms,
            )
            for record in result.tool_calls
        ],
        validation=ValidationResult(price_validator=price, groundedness=groundedness, scope=scope),
        final_reply=result.final_reply,
        retrieved=[
            RetrievedItem(
                id=hit.id, kind="product" if hit.kind == "product" else "fact", score=hit.score
            )
            for hit in result.retrieved
        ],
        model_calls=[
            ModelCallMeta(
                provider=call.provider,
                model=call.model,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                latency_ms=call.latency_ms,
                replayed=call.replayed,
                **({"cost_usd": call.cost_usd} if call.cost_usd is not None else {}),
            )
            for call in result.model_calls
        ],
    )
