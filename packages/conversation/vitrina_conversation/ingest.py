"""WhatsApp webhook ingest: check, keep, queue, acknowledge (FR-021, AS-3.1; FR-028).

The signature is checked on the body bytes as received, before anything else; a body that
fails is refused and not kept (it is logged by size and digest only). A signed body is kept
as received (``webhook_payloads``, 30 days), and each message is queued once for
``process_inbound``: the queue is keyed by the WhatsApp message id, so Meta's redeliveries add
nothing. Status callbacks are applied right away. A signed body this module cannot read is
kept with its error and still acknowledged, because Meta would only send it again; so is a
body with one unreadable message, whose other messages are queued. A message written with a
WhatsApp username and no number is kept but not queued: contacts, consent and replies go by
number.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from vitrina_conversation.store import ConversationStore
from vitrina_whatsapp.signature import is_valid_signature
from vitrina_whatsapp.webhook import MAX_BODY_BYTES, WebhookPayloadError, parse_webhook

logger = logging.getLogger(__name__)

SellerResolver = Callable[[str, str], Awaitable[UUID | None]]
"""``(phone_number_id, business_number) -> seller_id`` for the shop behind that number."""


class InvalidSignatureError(Exception):
    """The body is not signed with this app's secret (AS-3.1: 401)."""


class PayloadTooLargeError(Exception):
    """Larger than any webhook Meta sends."""


class InboundQueue(Protocol):
    async def enqueue(self, seller_id: UUID, payload_id: UUID, message_id: str) -> None: ...


@dataclass(frozen=True)
class IngestResult:
    payload_id: UUID | None
    queued: tuple[str, ...] = ()
    statuses: int = 0


@dataclass(frozen=True)
class Ingest:
    store: ConversationStore
    queue: InboundQueue
    sellers: SellerResolver
    app_secret: str

    async def receive(self, body: bytes, signature: str | None) -> IngestResult:
        if len(body) > MAX_BODY_BYTES:
            raise PayloadTooLargeError(f"{len(body)} bytes")
        if not is_valid_signature(body, signature, self.app_secret):
            logger.warning(
                "webhook refused: bad X-Hub-Signature-256 (%d bytes, sha256 %s)",
                len(body),
                hashlib.sha256(body).hexdigest()[:16],
            )
            raise InvalidSignatureError
        decoded = _decode(body)
        try:
            batch = parse_webhook(body)
        except WebhookPayloadError as error:
            logger.warning("webhook kept unread: %s", error)
            payload_id = await self.store.save_webhook(
                decoded, signature_valid=True, error=str(error)
            )
            return IngestResult(payload_id)
        if batch.unreadable:
            logger.warning("webhook partly unread: %s", "; ".join(batch.unreadable))
        payload_id = await self.store.save_webhook(
            decoded, signature_valid=True, error="; ".join(batch.unreadable) or None
        )

        queued: list[str] = []
        for message in batch.messages:
            if message.from_wa_id is None:
                logger.warning(
                    "message %s came from a username without a number; kept, not queued",
                    message.message_id,
                )
                continue
            seller_id = await self.sellers(message.phone_number_id, message.business_number)
            if seller_id is None:
                logger.warning(
                    "no shop uses phone number id %s; message not queued", message.phone_number_id
                )
                continue
            await self.queue.enqueue(seller_id, payload_id, message.message_id)
            queued.append(message.message_id)
        applied = 0
        for status in batch.statuses:
            seller_id = await self.sellers(status.phone_number_id, status.business_number)
            if seller_id is not None and await self.store.apply_status(
                seller_id, status.message_id, status.status
            ):
                applied += 1
        return IngestResult(payload_id, tuple(queued), applied)


def _decode(body: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(body)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        return decoded
    return {"unreadable_body": body.decode("utf-8", errors="replace")[:10_000]}
