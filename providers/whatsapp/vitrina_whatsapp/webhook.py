"""WhatsApp Cloud API webhook payloads → inbound messages and status updates (FR-021, FR-028).

Meta batches notifications (one POST may carry several entries, changes, messages and statuses)
and retries any delivery that did not get a ``200``, for up to 7 days, so the same message can
arrive more than once: consumers deduplicate on ``InboundMessage.message_id`` (AS-3.1). Changes
whose ``field`` is not ``messages`` (template reviews, account alerts) are listed in
``ignored_fields`` rather than rejected.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    from_wa_id: str
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


@dataclass(frozen=True, slots=True)
class MetaError:
    code: int
    title: str | None
    details: str | None


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    message_id: str
    phone_number_id: str
    recipient_wa_id: str
    status: WebhookStatus
    occurred_at: datetime
    errors: tuple[MetaError, ...]


@dataclass(frozen=True, slots=True)
class WebhookBatch:
    messages: tuple[InboundMessage, ...] = ()
    statuses: tuple[StatusUpdate, ...] = ()
    ignored_fields: tuple[str, ...] = ()

    @property
    def message_ids(self) -> tuple[str, ...]:
        return tuple(message.message_id for message in self.messages)


def parse_webhook(body: bytes | str | Mapping[str, Any]) -> WebhookBatch:
    """Read one webhook notification: raw bytes as received, its text, or the decoded JSON.

    Raises ``WebhookPayloadError`` when the envelope or a ``messages`` change is malformed.
    """
    try:
        envelope = (
            _Envelope.model_validate(body)
            if isinstance(body, Mapping)
            else _Envelope.model_validate_json(body)
        )
        changes = [change for entry in envelope.entry for change in entry.changes]
        values = [_Value.model_validate(c.value) for c in changes if c.field == "messages"]
    except ValidationError as exc:
        raise WebhookPayloadError(_describe(exc)) from exc

    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    for value in values:
        names = {c.wa_id: c.profile.name if c.profile else None for c in value.contacts}
        messages.extend(_inbound(m, value.metadata, names) for m in value.messages)
        # Statuses outside the four documented ones carry nothing to store; the raw payload keeps them.
        statuses.extend(
            _status(s, value.metadata) for s in value.statuses if s.status in _WEBHOOK_STATUSES
        )
    ignored = tuple(change.field for change in changes if change.field != "messages")
    return WebhookBatch(tuple(messages), tuple(statuses), ignored)


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


def _inbound(
    message: "_Message", metadata: "_Metadata", names: Mapping[str, str | None]
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
        profile_name=names.get(message.from_),
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
    )


def _status(status: "_Status", metadata: "_Metadata") -> StatusUpdate:
    return StatusUpdate(
        message_id=status.id,
        phone_number_id=metadata.phone_number_id,
        recipient_wa_id=status.recipient_id,
        status=cast(WebhookStatus, status.status),  # the caller keeps documented statuses only
        occurred_at=datetime.fromtimestamp(status.timestamp, tz=UTC),
        errors=tuple(
            MetaError(e.code, e.title, e.error_data.details if e.error_data else None)
            for e in status.errors
        ),
    )


def _describe(exc: ValidationError) -> str:
    """Locations and reasons only: input values may hold phone numbers or chat text (NFR-04)."""
    problems = (
        f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    )
    return "not a WhatsApp Business Account webhook payload: " + "; ".join(problems)


# ── Wire format (Meta's documented webhook shapes; unknown fields are ignored) ──


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True)


class _Profile(_Wire):
    name: str | None = None


class _Contact(_Wire):
    wa_id: str
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
    from_: str = Field(alias="from")
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


class _Status(_Wire):
    id: str
    status: str
    timestamp: int
    recipient_id: str
    errors: tuple[_Error, ...] = ()


class _Value(_Wire):
    metadata: _Metadata
    contacts: tuple[_Contact, ...] = ()
    messages: tuple[_Message, ...] = ()
    statuses: tuple[_Status, ...] = ()


class _Change(_Wire):
    field: str
    value: dict[str, Any]


class _Entry(_Wire):
    id: str
    changes: tuple[_Change, ...] = ()


class _Envelope(_Wire):
    object: Literal["whatsapp_business_account"]
    entry: tuple[_Entry, ...]
