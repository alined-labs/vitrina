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

When the turn fails, for any reason, the customer still gets an answer: the fallback reply, or
what the tools had already done, and a handoff so the owner picks up (constitution Art. III.3,
Art. VIII).

A job can come back for a message it already stored: after a crash, a timeout or a throttled
send. A reply's row has an id derived from its message's, and its ``delivery_status`` says how
far it got. No reply row: the message is processed again. No status: it never left, so it goes
out now; this is also how a reply stays when the owner took over while the turn ran. ``queued``:
the send began and nothing came back, so it may have reached the customer. It is not sent
again, because the Cloud API has no idempotency key and a repeat is the worse mistake here
(``vitrina_whatsapp.client``); the owner gets a handoff to look at the chat instead. Any other
status: done, a redelivery sends nothing (AS-3.1).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_OID, UUID, uuid5

from vitrina_catalog.api import SellerSettings
from vitrina_compliance.api import (
    ComplianceStore,
    ConsentRefusedError,
    Contact,
    opt_out,
    record_consent,
)
from vitrina_conversation.attribution import AdLookup, attribute, chat_started_payload
from vitrina_conversation.engine import (
    AgentDeps,
    Exchange,
    TurnRequest,
    TurnResult,
    made_confirmation,
    run_turn,
)
from vitrina_conversation.image import PhotoReader, PhotoReading
from vitrina_conversation.normalize import NormalizedImage, normalize
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.prompts import AdContext
from vitrina_conversation.store import (
    ConversationRecord,
    ConversationState,
    ConversationStore,
    Event,
    StoredMessage,
)
from vitrina_conversation.tools import HandoffAction, QueuedAsset, TurnActions
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
    can_retry: bool = False
    """A job will come back for this message if this attempt raises; nothing else ever does."""


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
    reply_id: UUID
    opted_out: bool

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
        try:
            return await self._process(contact)
        except Exception, asyncio.CancelledError:
            if not inbound.can_retry:
                await self._report_last_attempt()
            raise

    async def _process(self, contact: Contact) -> InboundOutcome:
        deps, inbound, conversation = self.deps, self.inbound, self.conversation
        recent = await deps.store.recent_messages(
            self.seller_id, conversation.id, limit=deps.history_turns * 2 + 1
        )
        # A job's retry finds its own message already there.
        earlier = [message for message in recent if message.wa_message_id != inbound.message_id]
        # Q31: once the customer opted out, no consent question is pending or asked again.
        opted_out = contact.opted_out_at is not None
        pending = _consent_pending(earlier) and not opted_out
        pending_prompt = self.seller.consent_prompt_text if pending else None
        self.opted_out = opted_out
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
        if stored is None:  # Meta's redelivery (AS-3.1), or this job back for an unfinished try
            stored = await deps.store.message_by_wa_id(self.seller_id, inbound.message_id)
            if stored is None or stored.conversation_id != conversation.id:
                return InboundOutcome("duplicate", conversation.id)
            reply = await deps.store.message(self.seller_id, _reply_id(stored.id))
            if reply is not None:
                return await self._resume(reply)
        self.evidence = stored.id
        self.reply_id = _reply_id(stored.id)
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

        actions = TurnActions()
        turn: TurnResult | None = None
        try:
            request = TurnRequest(
                seller=self.seller,
                conversation_id=conversation.id,
                message=question,
                history=_exchanges(earlier),
                dry_run=False,
                low_confidence_streak=conversation.low_confidence_streak,
                pending_consent_prompt=pending_prompt,
                ad=await self._ad(),
                opted_out=self.opted_out,
            )
            turn = await run_turn(request, deps.agent, actions)
        except asyncio.CancelledError:
            # The job's time ran out or it is stopping, which says nothing about the turn: no
            # fallback, the job comes back and resumes. The opt-out cannot wait for that.
            await self._record_opt_out(actions, question)
            raise
        except Exception as error:
            logger.warning(
                "agent turn failed (%s); falling back to the owner", type(error).__name__
            )
        # Outside the fallback: if this fails, the job comes back rather than end without it.
        await self._record_opt_out(actions, question)
        try:
            await self._apply(actions)
            if turn is not None:
                await self._finish_turn(turn)
        except Exception as error:
            logger.warning(
                "agent turn not applied (%s); falling back to the owner", type(error).__name__
            )
            turn = None
        if turn is None:
            return await self._unavailable(question, actions)
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

    async def _record_opt_out(self, actions: TurnActions, text: str) -> None:
        if actions.opt_out is None:
            return
        await opt_out(
            self.deps.compliance,
            self.seller_id,
            self.contact_id,
            via="tool",
            response_text=text,
            evidence_message_id=self.evidence,
        )
        if actions.opt_out.erase and self.deps.request_erasure is not None:
            await self.deps.request_erasure(self.seller_id, self.contact_id)

    async def _apply(self, actions: TurnActions) -> None:
        """What the tools did and decided, even if the turn then failed. Consent is not among
        it: R8 records it from the customer's own reply above, and the model's record_consent
        call answers by the same rules."""
        deps, conversation = self.deps, self.conversation
        ad_id = conversation.attribution.ad_id
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

    async def _finish_turn(self, turn: TurnResult) -> None:
        await self.deps.store.update_conversation(
            self.seller_id,
            self.conversation.id,
            touched_at=self.deps.clock(),
            low_confidence_streak=turn.low_confidence_streak,
        )
        if turn.actions.handoff is not None and not await self._owner_took_over():
            await self._hand_off(turn.actions.handoff)

    async def _hand_off(self, handoff: HandoffAction) -> None:
        conversation = self.conversation
        events: list[Event] = []
        # A second handoff on an open one is a note for the owner, not another for the ad.
        if conversation.attribution.ad_id is not None and not _handoff_open(await self._record()):
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

    async def _unavailable(self, question: str, actions: TurnActions) -> InboundOutcome:
        if await self._owner_took_over():  # R6
            return InboundOutcome("human_mode", self.conversation.id)
        summary = (
            f"Motivo: el asistente no estuvo disponible. Último mensaje del cliente: «{question}»"
        )[:500]
        await self._hand_off(actions.handoff or HandoffAction(new_id(), "other", summary))
        return await self._reply(self._after_failure(actions), "replied")

    def _after_failure(self, actions: TurnActions) -> str:
        """What the tools did before the turn failed, or else the fallback."""
        done: list[str] = []
        if actions.bookings or actions.reservations:
            done.append(made_confirmation(self.seller, actions.bookings, actions.reservations))
        if actions.opt_out is not None:
            done.append(ERASURE_REPLY if actions.opt_out.erase else OPT_OUT_REPLY)
        return " ".join(done) or FALLBACK_REPLY.format(owner_name=self.seller.owner_name)

    async def _report_last_attempt(self) -> None:
        """Nothing comes back for this message after this try, so the owner has to hear of it."""
        summary = (
            "Motivo: no se pudo atender este mensaje. "
            f"Último mensaje del cliente: «{self.inbound.text or ''}»"
        )[:500]
        try:
            await self._hand_off(HandoffAction(new_id(), "other", summary))
        except Exception as error:
            logger.warning("the owner was not told of a lost message (%s)", type(error).__name__)

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
            is_replay=inbound.is_replay,
            message_id=self.reply_id,
        )
        assert stored is not None  # outbound rows carry no WhatsApp id yet
        return await self._deliver(stored.id, text, status, turn, guarded=turn is not None)

    async def _resume(self, reply: StoredMessage) -> InboundOutcome:
        """This message already has its reply row: see the module docstring for each status."""
        conversation_id = self.conversation.id
        if reply.text is None or reply.delivery_status not in (None, "queued"):
            return InboundOutcome("duplicate", conversation_id)
        if reply.delivery_status == "queued":
            why = "no se pudo confirmar si la respuesta llegó al cliente"
            await self._undelivered(reply.id, reply.text, why)
            return InboundOutcome("send_failed", conversation_id, reply.text)
        return await self._deliver(reply.id, reply.text, "replied", guarded=reply.sender == "agent")

    async def _deliver(
        self,
        message_id: UUID,
        text: str,
        status: OutcomeStatus,
        turn: TurnResult | None = None,
        *,
        guarded: bool,
    ) -> InboundOutcome:
        conversation_id = self.conversation.id
        message = OutboundText(to=self.recipient, text=text)
        try:
            sent = await self._send(message_id, message, guarded=guarded)
        except ProviderError as error:
            if error.retryable and self.inbound.can_retry:
                # It did not go; the job comes back for this same row, after any pause WhatsApp
                # asked for. Not a new turn, so the customer gets the reply already made.
                await self.deps.store.set_delivery(self.seller_id, message_id, status=None)
                raise
            await self._undelivered(message_id, text, _why_not_sent(error))
            return InboundOutcome("send_failed", conversation_id, text, turn)
        if not sent:
            return InboundOutcome("human_mode", conversation_id, None, turn)
        return InboundOutcome(status, conversation_id, text, turn)

    async def _undelivered(self, message_id: UUID, text: str | None, why: str) -> None:
        """The owner sees it. The handoff comes first: dying between the two repeats it, while
        the other order would lose it."""
        summary = f"Motivo: {why}. Mensaje sin entregar: «{text or ''}»"[:500]
        await self._hand_off(HandoffAction(new_id(), "other", summary))
        await self.deps.store.set_delivery(self.seller_id, message_id, status="failed")

    async def _record(self) -> ConversationRecord | None:
        return await self.deps.store.get_conversation(self.seller_id, self.conversation.id)

    async def _owner_took_over(self) -> bool:
        """R6, read again: a turn takes seconds. A turn's own handoff also makes the chat human
        and still gets its reply; a takeover opens no handoff (AS-3.9)."""
        record = await self._record()
        return record is None or (record.state.mode != "agent" and not _handoff_open(record))

    async def _send_assets(self, assets: Sequence[QueuedAsset]) -> None:
        """T043: each image is stored under the id the tool gave the model; its ``media_ref``
        is ``ad:<ad_id>``, for the dashboard to show the ad (``GET /ads/{ad_id}``). A failed one
        goes to the owner and is not retried: the image is not kept for a job to send later."""
        for asset in assets:
            stored = await self.deps.store.add_message(
                self.seller_id,
                self.conversation.id,
                direction="outbound",
                sender="agent",
                type="image",
                text=asset.caption,
                is_replay=self.inbound.is_replay,
                message_id=asset.message_id,
                media_key=f"ad:{asset.ad_id}",
            )
            assert stored is not None
            image = OutboundImage(to=self.recipient, image=asset.image, caption=asset.caption)
            try:
                await self._send(stored.id, image, guarded=True)
            except ProviderError as error:
                why = f"{_why_not_sent(error)}; no llegó una imagen del anuncio"
                await self._undelivered(stored.id, asset.caption, why)
                return

    async def _send(
        self, message_id: UUID, message: OutboundText | OutboundImage, *, guarded: bool
    ) -> bool:
        """One paced send (R12), False when ``guarded`` and the owner took over (the row stays,
        without a status, for the record). ``queued`` marks the send as begun, and it stays if
        this task is cancelled or dies before the answer is recorded (FR-028)."""
        deps = self.deps
        async with deps.pacer.slot(message.to):
            if guarded and await self._owner_took_over():
                return False
            await deps.store.set_delivery(self.seller_id, message_id, status="queued")
            try:
                sent = await deps.messenger.send(message)
            except ProviderError as error:
                logger.warning(
                    "%s message not sent (%s, retryable=%s)", message.kind, error, error.retryable
                )
                raise
        await deps.store.set_delivery(
            self.seller_id, message_id, status=sent.status, wa_message_id=sent.message_id
        )
        return True

    @property
    def recipient(self) -> str:
        return "+" + self.inbound.wa_id.removeprefix("+")


def _reply_id(inbound_id: UUID) -> UUID:
    """The id of a message's reply row, so a retry finds it whatever was stored since."""
    return uuid5(NAMESPACE_OID, f"reply:{inbound_id}")


def _handoff_open(record: ConversationRecord | None) -> bool:
    return record is not None and record.handoff is not None and record.handoff.released_at is None


def _why_not_sent(error: ProviderError) -> str:
    return "WhatsApp limitó los envíos" if error.retryable else "WhatsApp rechazó el mensaje"


def _unsent(message: StoredMessage) -> bool:
    """A reply the customer never got: withheld after a takeover, or a failed send."""
    return message.direction == "outbound" and message.delivery_status in (None, "failed")


def _consent_pending(recent: Sequence[StoredMessage]) -> bool:
    """R8: the last reply ended with the consent question; ad images sent after it are skipped,
    and so is a question that was never delivered."""
    for message in reversed(recent):
        if (message.direction == "outbound" and message.type == "image") or _unsent(message):
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
        elif customer and message.text and not _unsent(message):
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
