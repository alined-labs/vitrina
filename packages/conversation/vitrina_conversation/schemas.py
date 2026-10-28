"""openapi.yaml schemas of the agent and conversation routes, and the mappings to them.

``POST /agent/turns`` (FR-030) maps a ``TurnResult``; the conversation routes map store records.

The response's ``AgentTurnOutput`` is the API shape: three required fields and nullable as
a whole, null when no valid answer came back. The model's own answer is held to the strict
agent-tools.json shape (``contract.AgentTurnOutput``); research/OPEN.md Q15 and C2.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

from vitrina_conversation.contract import HandoffReason, ObjectionTag
from vitrina_conversation.engine import TurnResult
from vitrina_conversation.normalize import NormalizedMessage
from vitrina_conversation.store import ConversationRecord, StoredMessage


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
    passed, reason = result.scope_check
    scope = ScopeValidation(passed=passed, reason=reason)
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


# ── Conversations (openapi.yaml Conversation, Handoff, Message; AS-3.8, AS-3.9) ──

MessageType = Literal[
    "text", "image", "audio", "video", "document", "interactive", "template", "unsupported"
]
DeliveryStatus = Literal["queued", "sent", "delivered", "read", "failed", "mocked"]


class Handoff(BaseModel):
    id: UUID | MISSING = MISSING
    reason: HandoffReason | MISSING = MISSING
    summary: Annotated[str, Field(max_length=500)] | MISSING = MISSING
    opened_at: datetime | MISSING = MISSING
    released_at: datetime | None = None
    seller_notified: bool | MISSING = MISSING


class Conversation(BaseModel):
    id: UUID
    seller_id: UUID
    contact_id: UUID
    attributed_ad_id: UUID | None = None
    attributed_ad_code: str | None = None
    attribution_status: Literal["attributed", "unknown_code", "none"]
    mode: Literal["agent", "human", "closed"]
    handoff: Handoff | None = None
    last_message_at: datetime | MISSING = MISSING
    summary: str | MISSING = MISSING
    objection_tags: list[ObjectionTag] | MISSING = MISSING
    reservation_ids: list[UUID] | MISSING = MISSING
    is_replay: bool = False
    created_at: datetime


class Message(BaseModel):
    id: UUID
    conversation_id: UUID
    direction: Literal["inbound", "outbound"]
    sender: Literal["customer", "agent", "seller", "system"] | MISSING = MISSING
    wa_message_id: str | None = None
    type: MessageType
    text: str | None = None
    transcript: str | None = None
    media_ref: str | None = None
    normalized: NormalizedMessage | None = None
    agent_output: AgentTurnOutput | None = None
    delivery_status: DeliveryStatus | MISSING = MISSING
    is_replay: bool = False
    created_at: datetime


class HumanMessageCreate(BaseModel):
    text: Annotated[str, Field(max_length=4096)]


def conversation_view(record: ConversationRecord, *, ad_code: str | None) -> Conversation:
    state = record.state
    handoff = record.handoff
    return Conversation(
        id=state.id,
        seller_id=state.seller_id,
        contact_id=state.contact_id,
        attributed_ad_id=state.attribution.ad_id,
        attributed_ad_code=ad_code if state.attribution.status == "attributed" else None,
        attribution_status=state.attribution.status,
        mode=state.mode,
        handoff=(
            Handoff(
                id=handoff.id,
                reason=cast(HandoffReason, handoff.reason),
                opened_at=handoff.opened_at,
                released_at=handoff.released_at,
                seller_notified=handoff.seller_notified,
                summary=handoff.summary[:500] if handoff.summary else MISSING,
            )
            if handoff is not None
            else None
        ),
        last_message_at=record.last_message_at or MISSING,
        summary=record.summary or MISSING,
        objection_tags=[cast(ObjectionTag, tag) for tag in record.objection_tags],
        is_replay=state.is_replay,
        created_at=record.created_at,
    )


def message_view(message: StoredMessage) -> Message:
    assert message.created_at is not None  # every stored row has one
    return Message(
        id=message.id,
        conversation_id=message.conversation_id,
        direction=message.direction,
        sender=message.sender,
        wa_message_id=message.wa_message_id,
        type=cast(MessageType, message.type),
        text=message.text,
        transcript=message.transcript,
        media_ref=message.media_key,
        normalized=(
            NormalizedMessage.model_validate(message.normalized) if message.normalized else None
        ),
        agent_output=(
            AgentTurnOutput.model_validate(message.agent_output) if message.agent_output else None
        ),
        delivery_status=message.delivery_status or MISSING,
        is_replay=message.is_replay,
        created_at=message.created_at,
    )


# ── replay (T080): openapi.yaml /replay/fixtures and /replay/sessions, inline schemas ──


class ReplayFixtureView(BaseModel):
    fixture_id: str
    name: str
    description: str | MISSING = MISSING
    message_count: int


class ReplaySessionCreate(BaseModel):
    fixture_id: str
    speed: Annotated[float, Field(ge=0.1, le=10, description="Playback speed multiplier")] = 1
    use_recorded_model_outputs: Annotated[bool, Field(description="true = fully offline demo")] = (
        False
    )


class ReplaySessionStarted(BaseModel):
    session_id: UUID
    conversation_id: UUID
    job_id: UUID
