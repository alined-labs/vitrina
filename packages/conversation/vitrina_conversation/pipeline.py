"""The inbound pipeline: one customer message from WhatsApp to the agent's reply (T027).

plan.md §4.1, in order: dedupe by WhatsApp message id (AS-3.1) → a voice note transcribed and
its transcript kept on the message (T051, AS-3.6) → opt-out and erasure keywords before
anything else, spoken ones too (R7, FR-063) → normalize (FR-022) → attribution (FR-023,
AS-3.2) → in human mode the message is only stored (R6) → a yes to a pending consent
question is recorded (R8) → the agent turn (T024) → its decisions applied: handoff,
objections, consent, opt-out, reservations and bookings with their ``ad.*`` events → the reply sent with
pacing (R12, AS-3.11) and its delivery status kept (FR-028) → the approved ad images the
agent chose (``send_ad_asset``, T043), each in its own paced slot, only after an accepted
reply that hands nothing off. Media the agent cannot read yet gets a short note (FR-022;
a photo is described by Spark and matched to the catalog first, T050), and so does a voice
note Transcribe could not hear or a photo with no product to read; reactions get no reply.

When the model is unavailable the customer still gets an answer: the fallback reply, and a
handoff so the owner picks up (constitution Art. III.3, Art. VIII).
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from vitrina_catalog.api import SellerSettings
from vitrina_compliance.api import (
    ComplianceStore,
    ConsentRefusedError,
    opt_out,
    record_consent,
)
from vitrina_conversation.attribution import AdLookup, attribute, chat_started_payload
from vitrina_conversation.engine import (
    AdContext,
    AgentDeps,
    Exchange,
    TurnRequest,
    TurnResult,
    run_turn,
)
from vitrina_conversation.image import PhotoReader, PhotoReading
from vitrina_conversation.normalize import NormalizedImage, normalize
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.store import ConversationState, ConversationStore, Event, StoredMessage
from vitrina_conversation.tools import HandoffAction, QueuedAsset
from vitrina_conversation.validation import FALLBACK_REPLY
from vitrina_conversation.voice import Voice
from vitrina_core.ids import new_id
from vitrina_core.providers import Messenger, OutboundImage, OutboundText, ProviderError

logger = logging.getLogger(__name__)

OPT_OUT_REPLY = (
    "Listo, no te enviaremos más mensajes promocionales. Si quieres volver a recibirlos, "
    "escríbenos."
)
ERASURE_REPLY = "Recibimos tu solicitud. Borraremos tus datos en las próximas 24 horas."
CONSENT_REPLY = (
    "Listo, te avisaremos de novedades y promociones. Si en algún momento no quieres más "
    "mensajes, responde BAJA."
)
TEXT_ONLY_REPLY = "Por ahora solo puedo leer mensajes de texto. ¿Me escribes tu pregunta?"
VOICE_NOT_HEARD_REPLY = "No pude escuchar bien tu nota de voz. ¿Me escribes tu pregunta?"
PHOTO_NOT_SEEN_REPLY = "No logré ver un producto en la foto. ¿Me cuentas qué estás buscando?"

OutcomeStatus = Literal[
    "replied",
    "duplicate",
    "unknown_seller",
    "opted_out",
    "erasure_requested",
    "human_mode",
    "stored",
    "send_failed",
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Inbound:
    """One customer message, whatever channel delivered it (webhook, replay, mock phone)."""

    seller_id: UUID
    wa_id: str
    message_id: str
    kind: str
    """openapi.yaml ``MessageType`` as received (text, image, audio, interactive, …)."""
    text: str | None
    profile_name: str | None = None
    wa_type: str | None = None
    raw_payload_id: UUID | None = None
    is_replay: bool = False
    media_id: str | None = None
    """The Graph API id of an image, voice note or other media."""


@dataclass(frozen=True)
class PipelineDeps:
    store: ConversationStore
    compliance: ComplianceStore
    seller_settings: Callable[[UUID], Awaitable[SellerSettings | None]]
    agent: AgentDeps
    messenger: Messenger
    pacer: Pacer
    ad_lookup: AdLookup
    ad_details: Callable[[UUID, UUID], Awaitable[AdContext | None]] | None = None
    """``(seller_id, ad_id) -> AdContext`` with the ad's code and product (creative.api)."""
    request_erasure: Callable[[UUID, UUID], Awaitable[None]] | None = None
    voice: Voice | None = None
    """Voice notes to text (T051); without it they get the text-only note."""
    photos: PhotoReader | None = None
    """Customer photos to words and catalog matches (T050); without it only captions count."""
    clock: Callable[[], datetime] = _utcnow
    history_turns: int = 10


@dataclass(frozen=True)
class InboundOutcome:
    status: OutcomeStatus
    conversation_id: UUID | None = None
    reply: str | None = None
    turn: TurnResult | None = None


async def process_inbound(inbound: Inbound, deps: PipelineDeps) -> InboundOutcome:
    seller = await deps.seller_settings(inbound.seller_id)
    if seller is None:
        logger.warning("inbound message for an unknown seller; stored only in the raw payload")
        return InboundOutcome("unknown_seller")
    return await _Inbound(inbound, deps, seller).run()


class _Inbound:
    contact_id: UUID
    conversation: ConversationState
    evidence: UUID

    def __init__(self, inbound: Inbound, deps: PipelineDeps, seller: SellerSettings) -> None:
        self.inbound = inbound
        self.deps = deps
        self.seller = seller
        self.seller_id = seller.seller_id

    async def run(self) -> InboundOutcome:
        deps, inbound = self.deps, self.inbound
        contact = await deps.compliance.resolve_contact(
            self.seller_id, inbound.wa_id, display_name=inbound.profile_name
        )
        self.contact_id = contact.id
        conversation = await deps.store.open_conversation(
            self.seller_id, contact.id, is_replay=inbound.is_replay
        )
        self.conversation = conversation
        recent = await deps.store.recent_messages(
            self.seller_id, conversation.id, limit=deps.history_turns * 2 + 1
        )
        pending_prompt = self.seller.consent_prompt_text if _consent_pending(recent) else None
        normalized = normalize(inbound.kind, inbound.text, pending_consent_prompt=pending_prompt)
        stored = await deps.store.add_message(
            self.seller_id,
            conversation.id,
            direction="inbound",
            sender="customer",
            type=inbound.kind,
            text=inbound.text,
            wa_message_id=inbound.message_id,
            normalized=normalized.model_dump(mode="json"),
            is_replay=inbound.is_replay,
            raw_payload_id=inbound.raw_payload_id,
        )
        if stored is None:  # Meta redelivered a message we already have (AS-3.1)
            return InboundOutcome("duplicate", conversation.id)
        self.evidence = stored.id
        text = inbound.text or ""
        unheard = False
        if inbound.kind == "audio" and inbound.media_id and deps.voice is not None:
            transcript = await self._transcribe(deps.voice, inbound.media_id)
            unheard = transcript is None
            if transcript is not None:
                text = transcript
                normalized = normalize(inbound.kind, text, pending_consent_prompt=pending_prompt)
                await deps.store.set_transcript(
                    self.seller_id, stored.id, text, normalized=normalized.model_dump(mode="json")
                )

        if normalized.opt_out_detected:  # R7, FR-063: before anything else
            await opt_out(
                deps.compliance,
                self.seller_id,
                contact.id,
                via="keyword",
                response_text=text,
                evidence_message_id=stored.id,
            )
            return await self._reply(OPT_OUT_REPLY, "opted_out")
        if normalized.erasure_requested:
            if deps.request_erasure is not None:
                await deps.request_erasure(self.seller_id, contact.id)
            return await self._reply(ERASURE_REPLY, "erasure_requested")

        attribution, started = await attribute(
            conversation.attribution, text, self.seller_id, deps.ad_lookup
        )
        events: list[Event] = []
        if started and attribution.ad_id is not None:
            events.append(
                (
                    "ad.chat_started",
                    chat_started_payload(
                        attribution.ad_id, conversation.id, contact.id, deps.clock()
                    ),
                )
            )
        elif not recent:  # a new chat without an ad still starts once, with ad_id null
            events.append(
                (
                    "ad.chat_started",
                    chat_started_payload(None, conversation.id, contact.id, deps.clock()),
                )
            )
        await deps.store.update_conversation(
            self.seller_id,
            conversation.id,
            touched_at=deps.clock(),
            attribution=attribution,
            events=events,
        )
        self.conversation = replace(conversation, attribution=attribution)

        if conversation.mode != "agent":  # R6: the owner is answering
            return InboundOutcome("human_mode", conversation.id)
        if inbound.wa_type == "reaction":
            return InboundOutcome("stored", conversation.id)

        # R8. The yes is all the message holds (its lexicon): the agent could only restate
        # the subscription, uncited, which R3 would turn into the fallback.
        if (
            normalized.consent_affirmative
            and pending_prompt is not None
            and await self._record_consent(pending_prompt, text)
        ):
            return await self._reply(CONSENT_REPLY, "replied")

        question = normalized.text if isinstance(normalized.text, str) else ""
        if unheard:
            return await self._reply(VOICE_NOT_HEARD_REPLY, "replied")
        if inbound.kind == "image" and inbound.media_id and deps.photos is not None:
            reading = await self._read_photo(deps.photos, inbound.media_id)
            if reading is not None and reading.description is not None:
                image = NormalizedImage(
                    description=reading.description,
                    matched_product_ids=list(reading.matched_product_ids),
                )
                normalized = normalized.model_copy(update={"image": image})
                await deps.store.set_normalized(
                    self.seller_id, self.evidence, normalized.model_dump(mode="json")
                )
                seen = f"[Foto del cliente: {reading.description}]"
                question = f"{question.strip()}\n{seen}" if question.strip() else seen
            elif not question.strip():
                return await self._reply(PHOTO_NOT_SEEN_REPLY, "replied")
        if normalized.kind == "unsupported" or not question.strip():
            return await self._reply(TEXT_ONLY_REPLY, "replied")

        request = TurnRequest(
            seller=self.seller,
            conversation_id=conversation.id,
            message=question,
            history=_exchanges(recent),
            dry_run=False,
            low_confidence_streak=conversation.low_confidence_streak,
            pending_consent_prompt=pending_prompt,
            ad=await self._ad(),
        )
        try:
            turn = await run_turn(request, deps.agent)
        except (ProviderError, TimeoutError) as error:
            logger.warning(
                "agent turn failed (%s); falling back to the owner", type(error).__name__
            )
            return await self._unavailable(question)
        await self._apply(turn, question)
        outcome = await self._reply(turn.final_reply, "replied", turn)
        accepted = turn.review is not None and turn.review.outcome == "accept"
        if outcome.status == "replied" and accepted and turn.actions.handoff is None:
            await self._send_assets(turn.actions.ad_assets)
        return outcome

    async def _read_photo(self, photos: PhotoReader, media_id: str) -> PhotoReading | None:
        """None when the photo cannot be fetched or read; its caption may still be answered."""
        try:
            return await photos.read(self.seller_id, media_id)
        except (ProviderError, TimeoutError) as error:
            logger.warning("photo not read (%s)", type(error).__name__)
            return None

    async def _ad(self) -> AdContext | None:
        """The chat's ad for the agent; a code that matched no ad goes as a bare code."""
        attribution = self.conversation.attribution
        if attribution.status == "attributed" and attribution.ad_id is not None:
            if self.deps.ad_details is not None:
                return await self.deps.ad_details(self.seller_id, attribution.ad_id)
            return None
        if attribution.status == "unknown_code" and attribution.code is not None:
            return AdContext(attribution.code)
        return None

    async def _transcribe(self, voice: Voice, media_id: str) -> str | None:
        """None when the voice note cannot be heard (download, Transcribe, or only silence)."""
        try:
            transcript = await voice.transcribe(media_id)
        except (ProviderError, TimeoutError) as error:
            logger.warning("voice note not transcribed (%s)", type(error).__name__)
            return None
        return transcript or None

    async def _apply(self, turn: TurnResult, text: str) -> None:
        """The turn's decisions. Consent is not among them: R8 records it from the customer's
        own reply above, and the model's record_consent call answers by the same rules."""
        deps, conversation = self.deps, self.conversation
        ad_id = conversation.attribution.ad_id
        actions = turn.actions
        for objection in actions.objections:
            events: list[Event] = [
                (
                    "objection.recorded",
                    {
                        "objection_id": str(objection.id),
                        "tag": objection.tag,
                        "product_id": str(objection.product_id) if objection.product_id else None,
                    },
                )
            ]
            if ad_id is not None:
                events.append(
                    (
                        "ad.objection",
                        {
                            "ad_id": str(ad_id),
                            "conversation_id": str(conversation.id),
                            "objection_id": str(objection.id),
                            "tag": objection.tag,
                        },
                    )
                )
            await deps.store.add_objection(
                self.seller_id, conversation.id, objection, ad_id=ad_id, events=events
            )
        if ad_id is not None and actions.bookings:
            await deps.store.emit(
                self.seller_id,
                [
                    (
                        "ad.booking",
                        {
                            "ad_id": str(ad_id),
                            "conversation_id": str(conversation.id),
                            "booking_id": str(booking.booking_id),
                        },
                    )
                    for booking in actions.bookings
                ],
            )
        if ad_id is not None and actions.reservations:
            await deps.store.emit(
                self.seller_id,
                [
                    (
                        "ad.reservation",
                        {
                            "ad_id": str(ad_id),
                            "conversation_id": str(conversation.id),
                            "reservation_id": str(item.reservation_id),
                            "product_id": str(item.product_id),
                            "quantity": item.quantity,
                        },
                    )
                    for item in actions.reservations
                ],
            )
        if actions.opt_out is not None:
            await opt_out(
                deps.compliance,
                self.seller_id,
                self.contact_id,
                via="tool",
                response_text=text,
                evidence_message_id=self.evidence,
            )
            if actions.opt_out.erase and deps.request_erasure is not None:
                await deps.request_erasure(self.seller_id, self.contact_id)
        await deps.store.update_conversation(
            self.seller_id,
            conversation.id,
            touched_at=deps.clock(),
            low_confidence_streak=turn.low_confidence_streak,
        )
        if actions.handoff is not None:
            await self._hand_off(actions.handoff)

    async def _hand_off(self, handoff: HandoffAction) -> None:
        conversation = self.conversation
        events: list[Event] = []
        if conversation.attribution.ad_id is not None:
            events.append(
                (
                    "ad.handoff",
                    {
                        "ad_id": str(conversation.attribution.ad_id),
                        "conversation_id": str(conversation.id),
                        "handoff_id": str(handoff.id),
                        "reason": handoff.reason,
                    },
                )
            )
        await self.deps.store.open_handoff(self.seller_id, conversation.id, handoff, events=events)

    async def _unavailable(self, question: str) -> InboundOutcome:
        summary = (
            f"Motivo: el asistente no estuvo disponible. Último mensaje del cliente: «{question}»"
        )[:500]
        await self._hand_off(HandoffAction(new_id(), "other", summary))
        return await self._reply(
            FALLBACK_REPLY.format(owner_name=self.seller.owner_name), "replied"
        )

    async def _record_consent(self, prompt: str, response: str) -> bool:
        try:
            await record_consent(
                self.deps.compliance,
                self.seller_id,
                self.contact_id,
                prompt_text=prompt,
                response_text=response,
                evidence_message_id=self.evidence,
            )
        except ConsentRefusedError:
            return False
        return True

    async def _reply(
        self, text: str, status: OutcomeStatus, turn: TurnResult | None = None
    ) -> InboundOutcome:
        deps, inbound = self.deps, self.inbound
        output = turn.output.model_dump(mode="json") if turn and turn.output else None
        stored = await deps.store.add_message(
            self.seller_id,
            self.conversation.id,
            direction="outbound",
            sender="agent" if turn is not None else "system",
            type="text",
            text=text,
            agent_output=output,
            validation=_validation(turn),
            delivery_status="queued",
            is_replay=inbound.is_replay,
        )
        assert stored is not None  # outbound rows carry no WhatsApp id yet
        if not await self._send(stored.id, OutboundText(to=self.recipient, text=text)):
            return InboundOutcome("send_failed", self.conversation.id, text, turn)
        return InboundOutcome(status, self.conversation.id, text, turn)

    async def _send_assets(self, assets: Sequence[QueuedAsset]) -> None:
        """T043: each image is stored under the id the tool gave the model; its ``media_ref``
        is ``ad:<ad_id>``, for the dashboard to show the ad (``GET /ads/{ad_id}``)."""
        for asset in assets:
            stored = await self.deps.store.add_message(
                self.seller_id,
                self.conversation.id,
                direction="outbound",
                sender="agent",
                type="image",
                text=asset.caption,
                delivery_status="queued",
                is_replay=self.inbound.is_replay,
                message_id=asset.message_id,
                media_key=f"ad:{asset.ad_id}",
            )
            assert stored is not None
            image = OutboundImage(to=self.recipient, image=asset.image, caption=asset.caption)
            await self._send(stored.id, image)

    async def _send(self, message_id: UUID, message: OutboundText | OutboundImage) -> bool:
        """One paced send (R12); its delivery status is kept either way (FR-028)."""
        deps = self.deps
        async with deps.pacer.slot(message.to):
            try:
                sent = await deps.messenger.send(message)
            except ProviderError as error:
                logger.warning(
                    "%s message not sent (%s, retryable=%s)", message.kind, error, error.retryable
                )
                await deps.store.set_delivery(self.seller_id, message_id, status="failed")
                return False
        await deps.store.set_delivery(
            self.seller_id, message_id, status=sent.status, wa_message_id=sent.message_id
        )
        return True

    @property
    def recipient(self) -> str:
        return "+" + self.inbound.wa_id.removeprefix("+")


def _consent_pending(recent: Sequence[StoredMessage]) -> bool:
    """R8: the last reply ended with the consent question; ad images sent after it are skipped."""
    for message in reversed(recent):
        if message.direction == "outbound" and message.type == "image":
            continue
        return message.proposed_consent
    return False


def _exchanges(recent: Sequence[StoredMessage]) -> tuple[Exchange, ...]:
    """Earlier turns as (customer, reply) pairs; consecutive customer messages are joined."""
    exchanges: list[Exchange] = []
    customer: list[str] = []
    for message in recent:
        if message.direction == "inbound":
            if words := message.text or message.transcript:
                customer.append(words)
        elif customer and message.text:
            exchanges.append(Exchange("\n".join(customer), message.text))
            customer = []
    return tuple(exchanges)


def _validation(turn: TurnResult | None) -> dict[str, object] | None:
    if turn is None or turn.review is None:
        return None
    review = turn.review
    return {
        "price_validator": {
            "passed": review.price.passed,
            "amounts_in_reply": list(review.price.amounts_in_reply),
            "amounts_in_citations": list(review.price.amounts_in_citations),
            "violations": list(review.price.violations),
            "retried": review.retried,
        },
        "groundedness": {
            "passed": review.groundedness.passed,
            "uncited_claims": list(review.groundedness.uncited_claims),
        },
        "scope": dict(zip(("passed", "reason"), turn.scope_check, strict=True)),
        "outcome": review.outcome,
        "confidence": review.confidence,
    }
