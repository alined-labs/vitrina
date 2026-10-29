"""Agent loop v1 (T024; plan.md §4.1; FR-024, FR-025; ADR-0005, ADR-0006).

One turn: the scope classifier (T054): an off-topic message gets the canned decline and a
medical, legal or financial question goes to the owner unanswered, both without retrieval
(R10, R11) → retrieve the seller's active items for the message (R1) → Muse Spark with the
system prompt, recent history, the contract tools and ``AgentTurnOutput`` as
``response_format`` → up to ``max_tool_rounds`` tool rounds → parse the answer (fenced or
padded JSON is read; otherwise one repair call) → the checks outside the model (R2–R5),
with one guided retry on an invented amount. The whole turn has a time budget; a provider
that keeps failing or a spent budget raises, so the caller decides what the customer gets.

Opt-out keywords (R7) and human mode (R6) run before this, in the inbound pipeline; the turn
applies nothing itself: ``TurnResult.actions`` lists what it decided, except reservations,
which the tool makes at once outside a dry run.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from vitrina_catalog.api import (
    Catalog,
    Citation,
    ProductNotFoundError,
    SearchResult,
    SellerSettings,
)
from vitrina_conversation.booking import BookingStore
from vitrina_conversation.contract import (
    AgentTurnOutput,
    HandoffReason,
    ObjectionTag,
    output_schema,
    tool_specs,
)
from vitrina_conversation.prompts import (
    REPAIR_INSTRUCTION,
    AdContext,
    local_time,
    render_system_prompt,
)
from vitrina_conversation.scope import (
    ScopeVerdict,
    classify_scope,
    decline_reply,
    sensitive_reply,
)
from vitrina_conversation.tools import (
    AdAssets,
    ApprovedAds,
    BookedSlot,
    HandoffAction,
    ObjectionAction,
    ToolBox,
    ToolCallRecord,
    TurnActions,
)
from vitrina_conversation.validation import FALLBACK_REPLY, Review, retry_instruction, review
from vitrina_core.ids import new_id
from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ReasoningEffort,
    ResponseFormat,
    with_backoff,
)

REASONS_ES: dict[HandoffReason, str] = {
    "customer_request": "el cliente pidió hablar con una persona",
    "complaint_refund": "queja, devolución o reembolso",
    "payment_issue": "problema con un pago",
    "medical_topic": "pregunta sobre un tema médico",
    "low_confidence_twice": "el asistente no tuvo el dato dos veces seguidas",
    "negative_sentiment": "el cliente está molesto",
    "amount_over_threshold": "pedido por encima del monto definido",
    "price_unverified": "el asistente no pudo confirmar un precio",
    "policy_exception": "pide una excepción a la política",
    "other": "otro motivo",
}
_FIELDS = frozenset(AgentTurnOutput.model_fields)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Exchange:
    """One earlier exchange of the conversation: what the customer wrote, what they got."""

    customer: str
    reply: str


@dataclass(frozen=True)
class TurnRequest:
    seller: SellerSettings
    conversation_id: UUID
    message: str
    history: tuple[Exchange, ...] = ()
    dry_run: bool = True
    low_confidence_streak: int = 0
    pending_consent_prompt: str | None = None
    """The consent question the last outbound message asked, if still unanswered (R8)."""
    ad: AdContext | None = None
    """The ad the chat came from (FR-023); the engine looks up its product's name."""


@dataclass(frozen=True)
class AgentDeps:
    chat: ChatModel
    catalog: Catalog
    model: str
    """``MODEL_AGENT`` (``settings.model_id("agent")``); never a literal (Q01)."""
    max_tool_rounds: int = 4
    budget_s: float = 20.0
    top_k: int = 5
    history_turns: int = 10
    temperature: float | None = 0.2
    strict_schema: bool = True
    reasoning_effort: ReasoningEffort | None = None
    """For the agent's calls; the scope check has its own (``scope_reasoning_effort``)."""
    scope_reasoning_effort: ReasoningEffort | None = None
    approved_ads: ApprovedAds | None = None
    ad_assets: AdAssets | None = None
    bookings: BookingStore | None = None
    clock: Callable[[], datetime] = _utcnow
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass(frozen=True)
class TurnResult:
    conversation_id: UUID
    output: AgentTurnOutput | None
    """The answer the review judged last; None when no valid answer came back."""
    final_reply: str
    """What the customer gets after the checks and fallbacks."""
    review: Review | None
    cited_ids: tuple[UUID, ...]
    """Active items the final reply cites; empty for a fallback."""
    off_topic: bool
    tool_calls: tuple[ToolCallRecord, ...]
    retrieved: tuple[SearchResult, ...]
    model_calls: tuple[CallMeta, ...]
    actions: TurnActions
    low_confidence_streak: int
    scope: ScopeVerdict = field(default=ScopeVerdict(True))

    @property
    def handoff(self) -> HandoffAction | None:
        return self.actions.handoff

    @property
    def scope_check(self) -> tuple[bool, str]:
        """``ValidationResult.scope``: the classifier's verdict, or the agent's own decline."""
        if not self.scope.in_scope:
            return False, self.scope.reason
        if self.off_topic:
            return False, "off_topic: declined by the agent"
        return True, self.scope.reason


async def run_turn(request: TurnRequest, deps: AgentDeps) -> TurnResult:
    """Run one agent turn; ``TimeoutError`` when the budget runs out (Art. VIII.1)."""
    async with asyncio.timeout(deps.budget_s):
        return await _Turn(request, deps).run()


def booking_confirmation(seller: SellerSettings, booked: Sequence[BookedSlot]) -> str:
    """From the bookings the tool made, so it needs no citation."""
    slots = " y ".join(
        f"{slot.service} para el {local_time(slot.starts_at, seller.timezone)}" for slot in booked
    )
    made = "quedó tu cita" if len(booked) == 1 else "quedaron tus citas"
    return f"Listo, {made} de {slots}. ¿Te ayudo con algo más?"


def parse_output(content: str | None) -> AgentTurnOutput | None:
    """The answer as JSON, or the JSON object inside fences or prose, minus unknown keys."""
    if not content:
        return None
    try:
        return AgentTurnOutput.model_validate_json(content)
    except ValidationError:
        pass
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
        if not isinstance(data, dict):
            return None
        return AgentTurnOutput.model_validate({k: v for k, v in data.items() if k in _FIELDS})
    except ValueError, ValidationError:
        return None


class _DeclinedError(Exception):
    """The scope check turned the message down while the agent was working on it."""


class _Turn:
    def __init__(self, request: TurnRequest, deps: AgentDeps) -> None:
        self.request = request
        self.deps = deps
        self.calls: list[CallMeta] = []
        self.box = ToolBox(
            catalog=deps.catalog,
            seller=request.seller,
            conversation_id=request.conversation_id,
            dry_run=request.dry_run,
            message=request.message,
            pending_consent_prompt=request.pending_consent_prompt,
            approved_ads=deps.approved_ads,
            ad_assets=deps.ad_assets,
            bookings=deps.bookings,
            clock=deps.clock,
        )

    async def run(self) -> TurnResult:
        """The scope check runs beside retrieval and the first agent call (NFR-01): nothing the
        agent does reaches a tool or the customer before the verdict, and a verdict against the
        message cancels the agent (R10, R11)."""
        seller = self.request.seller
        self._scope = asyncio.create_task(
            classify_scope(
                self.request.message,
                seller,
                chat=self.deps.chat,
                model=self.deps.model,
                strict_schema=self.deps.strict_schema,
                reasoning_effort=self.deps.scope_reasoning_effort,
            )
        )
        agent = asyncio.create_task(self._agent())
        try:
            await asyncio.wait({self._scope, agent}, return_when=asyncio.FIRST_COMPLETED)
            scope = await self._scope
            if scope.call is not None:
                self.calls.insert(0, scope.call)
            if not scope.in_scope:  # R10
                return self._canned(scope, decline_reply(seller), objection_tag="off_topic")
            if scope.sensitive != "none":  # R11
                reason: HandoffReason = "medical_topic" if scope.sensitive == "medical" else "other"
                return self._canned(scope, sensitive_reply(seller), handoff_reason=reason)
            result = await agent
            return replace(result, model_calls=tuple(self.calls))  # the scope call first
        finally:
            for task in (self._scope, agent):
                task.cancel()
            await asyncio.gather(self._scope, agent, return_exceptions=True)

    async def _ad(self) -> AdContext | None:
        """The ad with its product's name; an ad whose product is gone reads as a bare code."""
        ad = self.request.ad
        if ad is None or ad.product_id is None or ad.product_name is not None:
            return ad
        try:
            product = await self.deps.catalog.get_product(
                self.request.seller.seller_id, ad.product_id
            )
        except ProductNotFoundError:
            return AdContext(ad.code)
        return replace(ad, product_name=product.name)

    async def _verdict(self) -> ScopeVerdict:
        """Waits for the scope check; ``_DeclinedError`` when the agent must stop."""
        scope = await asyncio.shield(self._scope)
        if not scope.in_scope or scope.sensitive != "none":
            raise _DeclinedError
        return scope

    async def _agent(self) -> TurnResult:
        seller = self.request.seller
        retrieved = await self.deps.catalog.search(
            seller.seller_id, self.request.message, top_k=self.deps.top_k
        )
        if self._scope.done():  # a rule or a quick verdict: no agent call it would discard
            await self._verdict()
        messages = [
            ChatMessage(
                role="system",
                content=render_system_prompt(
                    seller, retrieved, self.deps.clock(), ad=await self._ad()
                ),
            ),
            *self._history(),
            ChatMessage(role="user", content=self.request.message),
        ]
        output = await self._answer(messages)
        scope = await self._verdict()
        verdict: Review | None = None
        citations: list[Citation] = []
        if output is not None:
            citations = await self.deps.catalog.citations(seller.seller_id, output.cited_fact_ids)
            verdict = self._review(output, citations, retried=False, read=retrieved)
            if verdict.outcome == "retry":
                messages += [
                    ChatMessage(role="assistant", content=output.model_dump_json()),
                    ChatMessage(role="system", content=retry_instruction(verdict)),
                ]
                second = await self._answer(messages)
                if second is not None:
                    output = second
                    citations = await self.deps.catalog.citations(
                        seller.seller_id, output.cited_fact_ids
                    )
                verdict = self._review(output, citations, retried=True, read=retrieved)

        if verdict is None:  # no readable answer at all: confidence 0 (R3, R4)
            streak = self.request.low_confidence_streak + 1
            final = FALLBACK_REPLY.format(owner_name=seller.owner_name)
            handoff_reason: HandoffReason | None = "low_confidence_twice" if streak >= 2 else None
        else:
            streak = verdict.low_confidence_streak
            final = verdict.final_reply or FALLBACK_REPLY.format(owner_name=seller.owner_name)
            handoff_reason = verdict.handoff_reason
        accepted = verdict is not None and verdict.outcome == "accept"
        booked = self.box.actions.bookings
        if booked and not accepted and handoff_reason is None:
            # The tool booked it: the customer hears that, never "no tengo ese dato" (T052).
            final = booking_confirmation(seller, booked)

        actions = self.box.actions
        if handoff_reason is not None and actions.handoff is None:  # R5
            actions.handoff = HandoffAction(new_id(), handoff_reason, self._summary(handoff_reason))
        tag = output.objection_tag if output is not None else None
        if tag is not None and all(objection.tag != tag for objection in actions.objections):
            actions.objections.append(ObjectionAction(new_id(), tag, self.request.message[:200]))
        known = {citation.id for citation in citations}
        cited = (
            tuple(fact_id for fact_id in output.cited_fact_ids if fact_id in known)
            if accepted and output is not None
            else ()
        )
        return TurnResult(
            conversation_id=self.request.conversation_id,
            output=output,
            final_reply=final,
            review=verdict,
            cited_ids=cited,
            off_topic=accepted and output is not None and output.objection_tag == "off_topic",
            tool_calls=tuple(self.box.records),
            retrieved=tuple(retrieved),
            model_calls=tuple(self.calls),
            actions=actions,
            low_confidence_streak=streak,
            scope=scope,
        )

    def _canned(
        self,
        scope: ScopeVerdict,
        reply: str,
        *,
        objection_tag: ObjectionTag | None = None,
        handoff_reason: HandoffReason | None = None,
    ) -> TurnResult:
        """The engine's own answer, reviewed like the model's; nothing retrieved or answered by
        the agent is used."""
        output = AgentTurnOutput(
            reply=reply,
            cited_fact_ids=(),
            confidence=1.0,
            handoff_reason=handoff_reason,
            objection_tag=objection_tag,
            proposed_consent_prompt=False,
        )
        verdict = self._review(output, [], retried=False)
        actions = self.box.actions
        if handoff_reason is not None:
            actions.handoff = HandoffAction(new_id(), handoff_reason, self._summary(handoff_reason))
        if objection_tag is not None:
            actions.objections.append(
                ObjectionAction(new_id(), objection_tag, self.request.message[:200])
            )
        return TurnResult(
            conversation_id=self.request.conversation_id,
            output=output,
            final_reply=verdict.final_reply or reply,
            review=verdict,
            cited_ids=(),
            off_topic=objection_tag == "off_topic",
            tool_calls=(),
            retrieved=(),
            model_calls=tuple(self.calls),
            actions=actions,
            low_confidence_streak=verdict.low_confidence_streak,
            scope=scope,
        )

    def _history(self) -> list[ChatMessage]:
        recent = self.request.history[-self.deps.history_turns :] if self.deps.history_turns else ()
        messages: list[ChatMessage] = []
        for exchange in recent:
            messages.append(ChatMessage(role="user", content=exchange.customer))
            messages.append(ChatMessage(role="assistant", content=exchange.reply))
        return messages

    def _review(
        self,
        output: AgentTurnOutput,
        citations: Sequence[Citation],
        *,
        retried: bool,
        read: Sequence[SearchResult] = (),
    ) -> Review:
        seller = self.request.seller
        texts = {str(hit.id): hit.text for hit in read}
        for record in self.box.records:  # search_catalog results the agent read
            for hit in record.result.get("results", ()):
                if isinstance(hit, dict) and "id" in hit and "text" in hit:
                    texts[str(hit["id"])] = str(hit["text"])
        return review(
            output,
            citations,
            owner_name=seller.owner_name,
            business_name=seller.business_name,
            customer_message=self.request.message,
            consent_prompt=seller.consent_prompt_text,
            cited_texts=[texts[key] for key in map(str, output.cited_fact_ids) if key in texts],
            low_confidence_threshold=seller.low_confidence_threshold,
            low_confidence_streak=self.request.low_confidence_streak,
            retried=retried,
        )

    def _summary(self, reason: HandoffReason) -> str:
        """For the owner when the model handed off without writing a summary (R5)."""
        text = f"Motivo: {REASONS_ES[reason]}. Último mensaje del cliente: «{self.request.message}»"
        return text if len(text) <= 500 else text[:499] + "…"

    async def _answer(self, messages: list[ChatMessage]) -> AgentTurnOutput | None:
        """Tool rounds until a final answer; past the cap the model must answer (no tools)."""
        for round_number in range(self.deps.max_tool_rounds + 1):
            tools_allowed = round_number < self.deps.max_tool_rounds
            message = await self._complete(messages, "auto" if tools_allowed else "none")
            if message.tool_calls and tools_allowed:
                messages.append(message)
                await self._verdict()  # no tool runs on a message the scope check turns down
                for call in message.tool_calls:
                    record = await self.box.run(call)
                    messages.append(
                        ChatMessage(
                            role="tool",
                            tool_call_id=call.id,
                            content=json.dumps(record.result, ensure_ascii=False),
                        )
                    )
                continue
            output = parse_output(message.content if isinstance(message.content, str) else None)
            if output is not None:
                return output
            messages += [
                ChatMessage(role="assistant", content=message.content or ""),
                ChatMessage(role="system", content=REPAIR_INSTRUCTION),
            ]
            repaired = await self._complete(messages, "none")
            return parse_output(repaired.content if isinstance(repaired.content, str) else None)
        return None

    async def _complete(
        self, messages: list[ChatMessage], tool_choice: Literal["auto", "none"]
    ) -> ChatMessage:
        request = ChatRequest(
            model=self.deps.model,
            purpose="agent",
            messages=tuple(messages),
            tools=tool_specs(),
            tool_choice=tool_choice,
            response_format=ResponseFormat(
                name="AgentTurnOutput", json_schema=output_schema(), strict=self.deps.strict_schema
            ),
            temperature=self.deps.temperature,
            reasoning_effort=self.deps.reasoning_effort,
            # The seller's rules and tools are the long, fixed prefix of every turn, so one key
            # per seller serves it from Meta's prompt cache, at the cached rate.
            prompt_cache_key=f"agent:{self.request.seller.seller_id}",
        )
        response = await with_backoff(
            lambda: self.deps.chat.complete(request), sleep=self.deps.sleep
        )
        self.calls.append(response.meta)
        return response.message
