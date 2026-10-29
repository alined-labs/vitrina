"""WhatsApp Cloud API webhook payloads → inbound messages and status updates (FR-021, FR-028).

Meta batches notifications (one POST may carry several entries, changes, messages and statuses)
and retries any delivery that did not get a ``200``, for up to 7 days, so the same message can
arrive more than once: consumers deduplicate on ``InboundMessage.message_id`` (AS-3.1). Changes
whose ``field`` is not ``messages`` (template reviews, account alerts) are listed in
``ignored_fields`` rather than rejected. Each message and status is read on its own: one this
adapter cannot read is listed in ``unreadable``, and the rest of the delivery still comes
through.

A customer with a WhatsApp username may write without their number: Meta then leaves out
``from`` and ``wa_id`` and sends their business-scoped user ID (``from_user_id``,
``user_id``, ``recipient_user_id``; Meta's business-scoped user IDs guide, read 2026-10-29).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MessageKind = Literal["text", "image", "audio", "video", "document", "interactive", "unsupported"]
"""openapi.yaml ``MessageType`` for inbound messages (``template`` is outbound only)."""

DeliveryStatus = Literal["queued", "sent", "delivered", "read", "failed", "mocked"]
"""openapi.yaml ``Message.delivery_status``."""

WebhookStatus = Literal["sent", "delivered", "read", "failed"]
"""The delivery statuses Meta reports in status callbacks."""

# Meta: "Webhook payloads can be up to 3 MB" (Cloud API webhooks guide, read 2026-10-27).
MAX_BODY_BYTES = 3 * 1024 * 1024

_KINDS: dict[str, MessageKind] = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "video": "video",
    "document": "document",
    "interactive": "interactive",
    "button": "interactive",  # quick-reply button on a template
}
_WEBHOOK_STATUSES: frozenset[str] = frozenset({"sent", "delivered", "read", "failed"})
_PROGRESS: dict[DeliveryStatus, int] = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}


class WebhookPayloadError(ValueError):
    """The body is not a WhatsApp Business Account notification this adapter can read."""


@dataclass(frozen=True, slots=True)
class MediaRef:
    """Media to download from the Graph API by ``media_id`` (image, audio, video, document, sticker)."""

    media_id: str
    mime_type: str | None
    sha256: str | None
    voice: bool
    filename: str | None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    message_id: str
    phone_number_id: str
    business_number: str
    from_wa_id: str | None
    """The customer's number; None when they write with a username and Meta leaves it out."""
    profile_name: str | None
    sent_at: datetime
    wa_type: str
    kind: MessageKind
    text: str | None
    """Text body, media caption, or the title of the tapped reply."""
    media: MediaRef | None
    reply_id: str | None
    """ID of the tapped interactive reply, or the payload of a template quick-reply button."""
    context_message_id: str | None
    """The message this one replies to, when the customer quoted or tapped one of ours."""
    from_user_id: str | None = None
    """The customer's business-scoped user ID, when Meta sends one."""


@dataclass(frozen=True, slots=True)
class MetaError:
    code: int
    title: str | None
    details: str | None


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    message_id: str
    phone_number_id: str
    business_number: str
    recipient_wa_id: str | None
    status: WebhookStatus
    occurred_at: datetime
    errors: tuple[MetaError, ...]
    recipient_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class WebhookBatch:
    messages: tuple[InboundMessage, ...] = ()
    statuses: tuple[StatusUpdate, ...] = ()
    ignored_fields: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    """What could not be read, by location and reason only (``messages.2: id: Field required``)."""

    @property
    def message_ids(self) -> tuple[str, ...]:
        return tuple(message.message_id for message in self.messages)


def parse_webhook(body: bytes | str | Mapping[str, Any]) -> WebhookBatch:
    """Read one webhook notification: raw bytes as received, its text, or the decoded JSON.

    Raises ``WebhookPayloadError`` only when the envelope is malformed; a message, status or
    change that cannot be read is listed in ``unreadable`` instead.
    """
    try:
        envelope = (
            _Envelope.model_validate(body)
            if isinstance(body, Mapping)
            else _Envelope.model_validate_json(body)
        )
    except ValidationError as exc:
        raise WebhookPayloadError(_describe(exc)) from exc
    changes = [change for entry in envelope.entry for change in entry.changes]

    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    unreadable: list[str] = []
    for position, change in enumerate(c for c in changes if c.field == "messages"):
        try:
            value = _Value.model_validate(change.value)
        except ValidationError as exc:
            unreadable.append(_describe(exc, f"changes.{position}"))
            continue
        names = _names(value.contacts)
        for index, raw in enumerate(value.messages):
            try:
                message = _Message.model_validate(raw)
            except ValidationError as exc:
                unreadable.append(_describe(exc, f"messages.{index}"))
                continue
            messages.append(_inbound(message, value.metadata, names))
        for index, raw in enumerate(value.statuses):
            try:
                status = _Status.model_validate(raw)
            except ValidationError as exc:
                unreadable.append(_describe(exc, f"statuses.{index}"))
                continue
            # Statuses outside the four documented ones carry nothing to store; the raw payload
            # keeps them.
            if status.status in _WEBHOOK_STATUSES:
                statuses.append(_status(status, value.metadata))
    ignored = tuple(change.field for change in changes if change.field != "messages")
    return WebhookBatch(tuple(messages), tuple(statuses), ignored, tuple(unreadable))


def merge_delivery_status(
    current: DeliveryStatus | None, incoming: WebhookStatus
) -> DeliveryStatus:
    """Fold one status callback into a message's stored ``delivery_status`` (FR-028).

    Callbacks arrive late, twice or out of order, so a status never moves backwards: ``read``,
    ``failed`` and ``mocked`` are final, ``failed`` only replaces a message that was not yet
    delivered, and ``queued → sent → delivered → read`` only moves forward.
    """
    if current is None:
        return incoming
    if current in ("read", "failed", "mocked"):
        return current
    if incoming == "failed":
        return "failed" if current in ("queued", "sent") else current
    return incoming if _PROGRESS[incoming] > _PROGRESS[current] else current


def _names(contacts: tuple[dict[str, Any], ...]) -> dict[str, str | None]:
    """Profile names by number and by user ID; a contact that cannot be read only loses its name."""
    names: dict[str, str | None] = {}
    for raw in contacts:
        try:
            contact = _Contact.model_validate(raw)
        except ValidationError:
            continue
        name = contact.profile.name if contact.profile else None
        names.update({key: name for key in (contact.wa_id, contact.user_id) if key})
    return names


def _inbound(
    message: _Message, metadata: _Metadata, names: Mapping[str, str | None]
) -> InboundMessage:
    media = {
        "image": message.image,
        "audio": message.audio,
        "video": message.video,
        "document": message.document,
        "sticker": message.sticker,
    }.get(message.type)
    reply = None
    if message.interactive is not None:
        reply = message.interactive.button_reply or message.interactive.list_reply

    text: str | None = None
    reply_id: str | None = None
    if message.text is not None:
        text = message.text.body
    elif media is not None:
        text = media.caption
    elif reply is not None:
        text, reply_id = reply.title, reply.id
    elif message.button is not None:
        text, reply_id = message.button.text, message.button.payload

    return InboundMessage(
        message_id=message.id,
        phone_number_id=metadata.phone_number_id,
        business_number=metadata.display_phone_number,
        from_wa_id=message.from_,
        profile_name=next(
            (names[key] for key in (message.from_, message.from_user_id) if key and key in names),
            None,
        ),
        sent_at=datetime.fromtimestamp(message.timestamp, tz=UTC),
        wa_type=message.type,
        kind=_KINDS.get(message.type, "unsupported"),
        text=text,
        media=(
            MediaRef(media.id, media.mime_type, media.sha256, media.voice, media.filename)
            if media is not None
            else None
        ),
        reply_id=reply_id,
        context_message_id=message.context.id if message.context is not None else None,
        from_user_id=message.from_user_id,
    )


def _status(status: _Status, metadata: _Metadata) -> StatusUpdate:
    return StatusUpdate(
        message_id=status.id,
        phone_number_id=metadata.phone_number_id,
        business_number=metadata.display_phone_number,
        recipient_wa_id=status.recipient_id,
        status=cast(WebhookStatus, status.status),  # the caller keeps documented statuses only
        occurred_at=datetime.fromtimestamp(status.timestamp, tz=UTC),
        errors=tuple(
            MetaError(e.code, e.title, e.error_data.details if e.error_data else None)
            for e in status.errors
        ),
        recipient_user_id=status.recipient_user_id,
    )


def _describe(exc: ValidationError, where: str = "") -> str:
    """Locations and reasons only: input values may hold phone numbers or chat text (NFR-04)."""
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    )
    if where:
        return f"{where}: {problems}"
    return "not a WhatsApp Business Account webhook payload: " + problems


# ── Wire format (Meta's documented webhook shapes; unknown fields are ignored) ──


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True)


class _Profile(_Wire):
    name: str | None = None


class _Contact(_Wire):
    wa_id: str | None = None
    user_id: str | None = None
    profile: _Profile | None = None


class _Metadata(_Wire):
    display_phone_number: str
    phone_number_id: str


class _ErrorData(_Wire):
    details: str | None = None


class _Error(_Wire):
    code: int
    title: str | None = None
    error_data: _ErrorData | None = None


class _Text(_Wire):
    body: str


class _Media(_Wire):
    id: str
    mime_type: str | None = None
    sha256: str | None = None
    caption: str | None = None
    filename: str | None = None
    voice: bool = False


class _Reply(_Wire):
    id: str
    title: str | None = None


class _Interactive(_Wire):
    type: str
    button_reply: _Reply | None = None
    list_reply: _Reply | None = None


class _Button(_Wire):
    text: str | None = None
    payload: str | None = None


class _Context(_Wire):
    id: str | None = None


class _Message(_Wire):
    id: str
    from_: str | None = Field(default=None, alias="from")
    from_user_id: str | None = None
    timestamp: int
    type: str
    text: _Text | None = None
    image: _Media | None = None
    audio: _Media | None = None
    video: _Media | None = None
    document: _Media | None = None
    sticker: _Media | None = None
    interactive: _Interactive | None = None
    button: _Button | None = None
    context: _Context | None = None

    @model_validator(mode="after")
    def _has_a_sender(self) -> Self:
        if self.from_ is None and self.from_user_id is None:
            raise ValueError("no sender: neither from nor from_user_id")
        return self


class _Status(_Wire):
    id: str
    status: str
    timestamp: int
    recipient_id: str | None = None
    recipient_user_id: str | None = None
    errors: tuple[_Error, ...] = ()


class _Value(_Wire):
    """Messages, statuses and contacts stay raw here: each is read on its own."""

    metadata: _Metadata
    contacts: tuple[dict[str, Any], ...] = ()
    messages: tuple[dict[str, Any], ...] = ()
    statuses: tuple[dict[str, Any], ...] = ()


class _Change(_Wire):
    field: str
    value: dict[str, Any]


class _Entry(_Wire):
    id: str
    changes: tuple[_Change, ...] = ()


class _Envelope(_Wire):
    object: Literal["whatsapp_business_account"]
    entry: tuple[_Entry, ...]
