"""Mock messenger: outbound messages land in a JSONL outbox instead of WhatsApp.

The outbox is a file so the API process (phone view) sees what the worker sent.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from vitrina_core.providers import (
    OutboundImage,
    OutboundMessage,
    OutboundTemplate,
    OutboundText,
    SendResult,
)

MAX_ENTRIES = 500


class MockEntry(BaseModel):
    id: str
    direction: Literal["inbound", "outbound"]
    contact: str
    kind: Literal["text", "image", "template"]
    text: str | None = None
    caption: str | None = None
    image_data_uri: str | None = None
    image_url: str | None = None
    template_name: str | None = None
    created_at: datetime


class MockOutbox:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _append(self, entry: MockEntry) -> MockEntry:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry

    def record_outbound(self, message: OutboundMessage) -> MockEntry:
        entry = MockEntry(
            id=f"mock.{uuid4().hex}",
            direction="outbound",
            contact=message.to,
            kind=message.kind,
            created_at=datetime.now(UTC),
        )
        if isinstance(message, OutboundText):
            entry = entry.model_copy(update={"text": message.text})
        elif isinstance(message, OutboundImage):
            data_uri = None
            if message.image is not None:
                encoded = base64.b64encode(message.image.data).decode()
                data_uri = f"data:{message.image.mime_type};base64,{encoded}"
            entry = entry.model_copy(
                update={
                    "caption": message.caption,
                    "image_data_uri": data_uri,
                    "image_url": message.image_url,
                }
            )
        elif isinstance(message, OutboundTemplate):
            entry = entry.model_copy(update={"template_name": message.template_name})
        return self._append(entry)

    def record_inbound(self, contact: str, text: str) -> MockEntry:
        return self._append(
            MockEntry(
                id=f"mock.{uuid4().hex}",
                direction="inbound",
                contact=contact,
                kind="text",
                text=text,
                created_at=datetime.now(UTC),
            )
        )

    def entries(self, contact: str | None = None) -> list[MockEntry]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-MAX_ENTRIES:]
        entries = [MockEntry.model_validate(json.loads(line)) for line in lines if line.strip()]
        return [entry for entry in entries if contact is None or entry.contact == contact]


class LocalMockMessenger:
    """Messenger that never leaves the machine (constitution Art. VIII.3, ADR-0011)."""

    def __init__(self, outbox: MockOutbox) -> None:
        self.outbox = outbox

    async def send(self, message: OutboundMessage) -> SendResult:
        entry = await asyncio.to_thread(self.outbox.record_outbound, message)
        return SendResult(message_id=entry.id, status="mocked")
