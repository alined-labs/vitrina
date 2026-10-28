"""Conversations, messages, handoffs and objections, in memory or on Postgres (data-model.md §1).

Every query is scoped to the seller (constitution Art. V.3). The events a write reports
(``ad.*``, ``objection.recorded``) commit in the same transaction as the write on Postgres
(``vitrina_core.events.emit``); the in-memory store keeps them in a list. A WhatsApp message
id is unique: storing the same inbound message twice returns None (dedupe, AS-3.1). Status
callbacks never move a message's delivery status backwards (FR-028).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_conversation import tables as db
from vitrina_conversation.attribution import NO_ATTRIBUTION, Attribution
from vitrina_conversation.tools import HandoffAction, ObjectionAction
from vitrina_core.events import EventType, emit
from vitrina_core.ids import new_id
from vitrina_whatsapp.webhook import DeliveryStatus, WebhookStatus, merge_delivery_status

Mode = Literal["agent", "human", "closed"]
Direction = Literal["inbound", "outbound"]
Sender = Literal["customer", "agent", "seller", "system"]
Event = tuple[EventType, dict[str, Any]]

# What an objection quote may not keep (data-model.md objections.quote_redacted; NFR-05).
_PHONE = re.compile(r"\+?\d[\d .-]{6,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(quote: str) -> str:
    """Phone numbers and email addresses out of a customer's quote."""
    return _EMAIL.sub("[correo]", _PHONE.sub("[número]", quote))


@dataclass(frozen=True)
class ConversationState:
    id: UUID
    seller_id: UUID
    contact_id: UUID
    mode: Mode = "agent"
    attribution: Attribution = NO_ATTRIBUTION
    low_confidence_streak: int = 0
    is_replay: bool = False


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    conversation_id: UUID
    direction: Direction
    sender: Sender
    type: str
    text: str | None
    wa_message_id: str | None = None
    delivery_status: DeliveryStatus | None = None
    agent_output: dict[str, Any] | None = None
    created_at: datetime | None = None

    @property
    def proposed_consent(self) -> bool:
        """The reply ended with the seller's consent question (R8)."""
        return bool(self.agent_output and self.agent_output.get("proposed_consent_prompt"))


class ConversationStore(Protocol):
    async def save_webhook(
        self, body: Mapping[str, Any], *, signature_valid: bool, error: str | None = None
    ) -> UUID: ...

    async def webhook(self, payload_id: UUID) -> dict[str, Any] | None: ...

    async def open_conversation(
        self, seller_id: UUID, contact_id: UUID, *, is_replay: bool = False
    ) -> ConversationState: ...

    async def add_message(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        *,
        direction: Direction,
        sender: Sender,
        type: str,
        text: str | None,
        wa_message_id: str | None = None,
        normalized: Mapping[str, Any] | None = None,
        agent_output: Mapping[str, Any] | None = None,
        validation: Mapping[str, Any] | None = None,
        delivery_status: DeliveryStatus | None = None,
        is_replay: bool = False,
        raw_payload_id: UUID | None = None,
    ) -> StoredMessage | None: ...

    async def recent_messages(
        self, seller_id: UUID, conversation_id: UUID, *, limit: int
    ) -> list[StoredMessage]: ...

    async def update_conversation(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        *,
        touched_at: datetime,
        mode: Mode | None = None,
        attribution: Attribution | None = None,
        low_confidence_streak: int | None = None,
        events: Sequence[Event] = (),
    ) -> None: ...

    async def set_delivery(
        self,
        seller_id: UUID,
        message_id: UUID,
        *,
        status: DeliveryStatus,
        wa_message_id: str | None = None,
    ) -> None: ...

    async def apply_status(
        self, seller_id: UUID, wa_message_id: str, status: WebhookStatus
    ) -> bool: ...

    async def open_handoff(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        handoff: HandoffAction,
        *,
        events: Sequence[Event] = (),
    ) -> None: ...

    async def add_objection(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        objection: ObjectionAction,
        *,
        ad_id: UUID | None,
        events: Sequence[Event] = (),
    ) -> None: ...

    async def emit(self, seller_id: UUID, events: Sequence[Event]) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Message:
    seller_id: UUID
    stored: StoredMessage
    normalized: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None


@dataclass
class InMemoryConversationStore:
    """For tests and runs without a database; ``events`` lists what Postgres would emit."""

    clock: Callable[[], datetime] = _utcnow
    conversations: dict[UUID, ConversationState] = field(default_factory=dict)
    messages: list[_Message] = field(default_factory=list)
    webhooks: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    handoffs: list[tuple[UUID, HandoffAction]] = field(default_factory=list)
    objections: list[tuple[UUID, ObjectionAction, UUID | None]] = field(default_factory=list)
    events: list[tuple[UUID, EventType, dict[str, Any]]] = field(default_factory=list)

    async def save_webhook(
        self, body: Mapping[str, Any], *, signature_valid: bool, error: str | None = None
    ) -> UUID:
        payload_id = new_id()
        self.webhooks[payload_id] = {"body": dict(body), "signature_valid": signature_valid}
        return payload_id

    async def webhook(self, payload_id: UUID) -> dict[str, Any] | None:
        stored = self.webhooks.get(payload_id)
        return stored["body"] if stored else None

    async def open_conversation(
        self, seller_id: UUID, contact_id: UUID, *, is_replay: bool = False
    ) -> ConversationState:
        for conversation in reversed(self.conversations.values()):
            ours = (conversation.seller_id, conversation.contact_id) == (seller_id, contact_id)
            if ours and conversation.mode != "closed":
                return conversation
        conversation = ConversationState(new_id(), seller_id, contact_id, is_replay=is_replay)
        self.conversations[conversation.id] = conversation
        return conversation

    async def add_message(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        *,
        direction: Direction,
        sender: Sender,
        type: str,
        text: str | None,
        wa_message_id: str | None = None,
        normalized: Mapping[str, Any] | None = None,
        agent_output: Mapping[str, Any] | None = None,
        validation: Mapping[str, Any] | None = None,
        delivery_status: DeliveryStatus | None = None,
        is_replay: bool = False,
        raw_payload_id: UUID | None = None,
    ) -> StoredMessage | None:
        if wa_message_id is not None and any(
            row.stored.wa_message_id == wa_message_id for row in self.messages
        ):
            return None
        stored = StoredMessage(
            id=new_id(),
            conversation_id=self._owned(seller_id, conversation_id).id,
            direction=direction,
            sender=sender,
            type=type,
            text=text,
            wa_message_id=wa_message_id,
            delivery_status=delivery_status,
            agent_output=dict(agent_output) if agent_output is not None else None,
            created_at=self.clock(),
        )
        self.messages.append(
            _Message(
                seller_id,
                stored,
                dict(normalized) if normalized is not None else None,
                dict(validation) if validation is not None else None,
            )
        )
        return stored

    async def recent_messages(
        self, seller_id: UUID, conversation_id: UUID, *, limit: int
    ) -> list[StoredMessage]:
        rows = [
            row.stored
            for row in self.messages
            if row.seller_id == seller_id and row.stored.conversation_id == conversation_id
        ]
        return rows[-limit:] if limit else []

    async def update_conversation(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        *,
        touched_at: datetime,
        mode: Mode | None = None,
        attribution: Attribution | None = None,
        low_confidence_streak: int | None = None,
        events: Sequence[Event] = (),
    ) -> None:
        conversation = self._owned(seller_id, conversation_id)
        self.conversations[conversation_id] = replace(
            conversation,
            mode=mode or conversation.mode,
            attribution=attribution or conversation.attribution,
            low_confidence_streak=(
                conversation.low_confidence_streak
                if low_confidence_streak is None
                else low_confidence_streak
            ),
        )
        await self.emit(seller_id, events)

    async def set_delivery(
        self,
        seller_id: UUID,
        message_id: UUID,
        *,
        status: DeliveryStatus,
        wa_message_id: str | None = None,
    ) -> None:
        for row in self.messages:
            if row.seller_id == seller_id and row.stored.id == message_id:
                row.stored = replace(
                    row.stored,
                    delivery_status=status,
                    wa_message_id=wa_message_id or row.stored.wa_message_id,
                )

    async def apply_status(
        self, seller_id: UUID, wa_message_id: str, status: WebhookStatus
    ) -> bool:
        for row in self.messages:
            if row.seller_id == seller_id and row.stored.wa_message_id == wa_message_id:
                merged = merge_delivery_status(row.stored.delivery_status, status)
                row.stored = replace(row.stored, delivery_status=merged)
                return True
        return False

    async def open_handoff(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        handoff: HandoffAction,
        *,
        events: Sequence[Event] = (),
    ) -> None:
        conversation = self._owned(seller_id, conversation_id)
        self.handoffs.append((conversation_id, handoff))
        self.conversations[conversation_id] = replace(conversation, mode="human")
        await self.emit(seller_id, events)

    async def add_objection(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        objection: ObjectionAction,
        *,
        ad_id: UUID | None,
        events: Sequence[Event] = (),
    ) -> None:
        self._owned(seller_id, conversation_id)
        self.objections.append(
            (conversation_id, replace(objection, quote=redact(objection.quote)), ad_id)
        )
        await self.emit(seller_id, events)

    async def emit(self, seller_id: UUID, events: Sequence[Event]) -> None:
        self.events.extend((seller_id, kind, dict(payload)) for kind, payload in events)

    def _owned(self, seller_id: UUID, conversation_id: UUID) -> ConversationState:
        conversation = self.conversations.get(conversation_id)
        if conversation is None or conversation.seller_id != seller_id:
            raise LookupError(f"no conversation {conversation_id} for this seller")
        return conversation


class PostgresConversationStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def save_webhook(
        self, body: Mapping[str, Any], *, signature_valid: bool, error: str | None = None
    ) -> UUID:
        payload_id = new_id()
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.insert(db.webhook_payloads).values(
                    id=payload_id, signature_valid=signature_valid, body=dict(body), error=error
                )
            )
        return payload_id

    async def webhook(self, payload_id: UUID) -> dict[str, Any] | None:
        async with self.sessions() as session:
            body = await session.scalar(
                sa.select(db.webhook_payloads.c.body).where(db.webhook_payloads.c.id == payload_id)
            )
        return dict(body) if body is not None else None

    async def open_conversation(
        self, seller_id: UUID, contact_id: UUID, *, is_replay: bool = False
    ) -> ConversationState:
        async with self.sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        sa.select(db.conversations)
                        .where(
                            db.conversations.c.seller_id == seller_id,
                            db.conversations.c.contact_id == contact_id,
                            db.conversations.c.mode != "closed",
                        )
                        .order_by(
                            db.conversations.c.created_at.desc(), db.conversations.c.id.desc()
                        )
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                return _conversation(row)
            conversation_id = new_id()
            await session.execute(
                sa.insert(db.conversations).values(
                    id=conversation_id,
                    seller_id=seller_id,
                    contact_id=contact_id,
                    is_replay=is_replay,
                )
            )
        return ConversationState(conversation_id, seller_id, contact_id, is_replay=is_replay)

    async def add_message(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        *,
        direction: Direction,
        sender: Sender,
        type: str,
        text: str | None,
        wa_message_id: str | None = None,
        normalized: Mapping[str, Any] | None = None,
        agent_output: Mapping[str, Any] | None = None,
        validation: Mapping[str, Any] | None = None,
        delivery_status: DeliveryStatus | None = None,
        is_replay: bool = False,
        raw_payload_id: UUID | None = None,
    ) -> StoredMessage | None:
        message_id = new_id()
        output = dict(agent_output) if agent_output is not None else None
        values = {
            "id": message_id,
            "seller_id": seller_id,
            "conversation_id": conversation_id,
            "direction": direction,
            "sender": sender,
            "type": type,
            "text": text,
            "wa_message_id": wa_message_id,
            "normalized": dict(normalized) if normalized is not None else None,
            "agent_output": output,
            "validation": dict(validation) if validation is not None else None,
            "delivery_status": delivery_status,
            "is_replay": is_replay,
            "raw_payload_id": raw_payload_id,
        }
        async with self.sessions() as session, session.begin():
            created = await session.scalar(
                insert(db.messages)
                .values(values)
                .on_conflict_do_nothing(index_elements=[db.messages.c.wa_message_id])
                .returning(db.messages.c.created_at)
            )
        if created is None:
            return None
        return StoredMessage(
            message_id,
            conversation_id,
            direction,
            sender,
            type,
            text,
            wa_message_id,
            delivery_status,
            output,
            created,
        )

    async def recent_messages(
        self, seller_id: UUID, conversation_id: UUID, *, limit: int
    ) -> list[StoredMessage]:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(db.messages)
                        .where(
                            db.messages.c.seller_id == seller_id,
                            db.messages.c.conversation_id == conversation_id,
                        )
                        .order_by(db.messages.c.created_at.desc(), db.messages.c.id.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [_message(row) for row in reversed(rows)]

    async def update_conversation(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        *,
        touched_at: datetime,
        mode: Mode | None = None,
        attribution: Attribution | None = None,
        low_confidence_streak: int | None = None,
        events: Sequence[Event] = (),
    ) -> None:
        values: dict[str, Any] = {"last_message_at": touched_at}
        if mode is not None:
            values["mode"] = mode
        if attribution is not None:
            values["attribution_status"] = attribution.status
            values["attributed_ad_id"] = attribution.ad_id
        if low_confidence_streak is not None:
            values["low_confidence_streak"] = low_confidence_streak
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(db.conversations)
                .where(
                    db.conversations.c.id == conversation_id,
                    db.conversations.c.seller_id == seller_id,
                )
                .values(values)
            )
            for kind, payload in events:
                await emit(session, kind, payload, seller_id=seller_id)

    async def set_delivery(
        self,
        seller_id: UUID,
        message_id: UUID,
        *,
        status: DeliveryStatus,
        wa_message_id: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"delivery_status": status}
        if wa_message_id is not None:
            values["wa_message_id"] = wa_message_id
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(db.messages)
                .where(db.messages.c.id == message_id, db.messages.c.seller_id == seller_id)
                .values(values)
            )

    async def apply_status(
        self, seller_id: UUID, wa_message_id: str, status: WebhookStatus
    ) -> bool:
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    sa.select(db.messages.c.id, db.messages.c.delivery_status)
                    .where(
                        db.messages.c.seller_id == seller_id,
                        db.messages.c.wa_message_id == wa_message_id,
                    )
                    .with_for_update()
                )
            ).first()
            if row is None:
                return False
            merged = merge_delivery_status(row.delivery_status, status)
            if merged != row.delivery_status:
                await session.execute(
                    sa.update(db.messages)
                    .where(db.messages.c.id == row.id)
                    .values(delivery_status=merged)
                )
        return True

    async def open_handoff(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        handoff: HandoffAction,
        *,
        events: Sequence[Event] = (),
    ) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.insert(db.handoffs).values(
                    id=handoff.id,
                    seller_id=seller_id,
                    conversation_id=conversation_id,
                    reason=handoff.reason,
                    summary=handoff.summary[:500],
                    urgency=handoff.urgency,
                )
            )
            await session.execute(
                sa.update(db.conversations)
                .where(
                    db.conversations.c.id == conversation_id,
                    db.conversations.c.seller_id == seller_id,
                )
                .values(mode="human")
            )
            for kind, payload in events:
                await emit(session, kind, payload, seller_id=seller_id)

    async def add_objection(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        objection: ObjectionAction,
        *,
        ad_id: UUID | None,
        events: Sequence[Event] = (),
    ) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.insert(db.objections).values(
                    id=objection.id,
                    seller_id=seller_id,
                    conversation_id=conversation_id,
                    ad_id=ad_id,
                    product_id=objection.product_id,
                    tag=objection.tag,
                    quote_redacted=redact(objection.quote),
                )
            )
            for kind, payload in events:
                await emit(session, kind, payload, seller_id=seller_id)

    async def emit(self, seller_id: UUID, events: Sequence[Event]) -> None:
        if not events:
            return
        async with self.sessions() as session, session.begin():
            for kind, payload in events:
                await emit(session, kind, payload, seller_id=seller_id)


def _conversation(row: Mapping[Any, Any]) -> ConversationState:
    return ConversationState(
        id=row["id"],
        seller_id=row["seller_id"],
        contact_id=row["contact_id"],
        mode=row["mode"],
        attribution=Attribution(row["attribution_status"], row["attributed_ad_id"]),
        low_confidence_streak=row["low_confidence_streak"],
        is_replay=row["is_replay"],
    )


def _message(row: Mapping[Any, Any]) -> StoredMessage:
    return StoredMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        direction=row["direction"],
        sender=row["sender"],
        type=row["type"],
        text=row["text"],
        wa_message_id=row["wa_message_id"],
        delivery_status=row["delivery_status"],
        agent_output=row["agent_output"],
        created_at=row["created_at"],
    )
