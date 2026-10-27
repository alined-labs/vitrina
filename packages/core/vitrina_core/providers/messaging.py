"""Outbound WhatsApp messages: the Cloud API adapter and the local mock implement Messenger.

Pacing (≥ 6 s per recipient, R12) belongs to the conversation engine, not to the messenger.
"""

from typing import Annotated, Any, Literal, Protocol

from pydantic import Field

from vitrina_core.providers.base import Blob, Frozen


class OutboundText(Frozen):
    kind: Literal["text"] = "text"
    to: str = Field(description="Recipient in E.164")
    text: str


class OutboundImage(Frozen):
    kind: Literal["image"] = "image"
    to: str
    image: Blob | None = None
    image_url: str | None = None
    caption: str | None = None


class OutboundTemplate(Frozen):
    """Approved marketing template, the only way to write outside the 24 h window (FR-064)."""

    kind: Literal["template"] = "template"
    to: str
    template_name: str
    language: str = "es"
    components: tuple[dict[str, Any], ...] = ()


OutboundMessage = Annotated[
    OutboundText | OutboundImage | OutboundTemplate, Field(discriminator="kind")
]


class SendResult(Frozen):
    message_id: str
    status: Literal["queued", "sent", "mocked", "failed"]
    error: str | None = None


class Messenger(Protocol):
    async def send(self, message: OutboundMessage) -> SendResult: ...
