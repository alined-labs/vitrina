"""The conversation module's part of an erasure (T062 with MX0; AS-6.6, FR-065; CI's db job).

Everything the chat wrote about the contact loses its content: messages, the agent's answers
and their review, handoff summaries, objection quotes, booking names, and the raw webhook
bodies that carried the contact's messages or delivery statuses. Rows stay, so counts and
attribution remain; another customer's chat is untouched.
"""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_conversation.api import PostgresConversationStore
from vitrina_conversation.booking import PostgresBookings, SlotRequest, book_slot
from vitrina_conversation.erasure import erase_conversations
from vitrina_conversation.tables import (
    bookings,
    conversations,
    handoffs,
    messages,
    objections,
    webhook_payloads,
)
from vitrina_conversation.testing import webhook_fixture
from vitrina_conversation.tools import HandoffAction, ObjectionAction
from vitrina_core.ids import new_id

pytestmark = pytest.mark.db

LAURA, OTHER = "573000000001", "573000000002"


async def chat(
    store: PostgresConversationStore,
    seller_id: UUID,
    contact_id: UUID,
    number: str,
    wa_message_id: str,
) -> tuple[UUID, UUID]:
    """One inbound message from a recorded body and the agent's reply; (conversation, payload)."""
    conversation = await store.open_conversation(seller_id, contact_id)
    body = webhook_fixture("text_question_shipping").replace(LAURA.encode(), number.encode())
    payload_id = await store.save_webhook(json.loads(body), signature_valid=True)
    await store.add_message(
        seller_id,
        conversation.id,
        direction="inbound",
        sender="customer",
        type="text",
        text="¿cuánto vale el envío a Medellín?",
        wa_message_id=wa_message_id,
        normalized={"kind": "text", "text": "¿cuánto vale el envío a Medellín?"},
        raw_payload_id=payload_id,
    )
    await store.add_message(
        seller_id,
        conversation.id,
        direction="outbound",
        sender="agent",
        type="text",
        text="El envío dentro de Medellín vale $8.000.",
        agent_output={"reply": "El envío dentro de Medellín vale $8.000."},
        validation={"groundedness": {"uncited_claims": []}},
        delivery_status="sent",
    )
    return conversation.id, payload_id


async def test_db_as_6_6_the_chat_of_an_erased_contact_keeps_no_content(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller = await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000101",
            city="Medellín",
        ),
        auth_subject=None,
    )
    identity = ContactIdentity(
        pepper=SecretStr("erasure-db-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(db_sessions, identity)
    laura = await compliance.resolve_contact(seller.id, LAURA, display_name="Laura Prueba")
    other = await compliance.resolve_contact(seller.id, OTHER, display_name="Otra Prueba")
    store = PostgresConversationStore(db_sessions)
    erased_chat, erased_payload = await chat(
        store, seller.id, laura.id, LAURA, "wamid.VITRINA-ERASE-0001"
    )
    kept_chat, kept_payload = await chat(
        store, seller.id, other.id, OTHER, "wamid.VITRINA-ERASE-0002"
    )
    status_payload = await store.save_webhook(
        json.loads(webhook_fixture("status_delivered")), signature_valid=True
    )
    await store.open_handoff(
        seller.id,
        erased_chat,
        HandoffAction(new_id(), "customer_request", "Laura quiere hablar con Ana."),
    )
    await store.add_objection(
        seller.id, erased_chat, ObjectionAction(new_id(), "price", "muy caro para mí"), ad_id=None
    )
    seller_settings = await PostgresSellerStore(db_sessions).settings(seller.id)
    assert seller_settings is not None
    open_saturday = seller_settings.model_copy(
        update={"business_hours": {"sat": [["09:00", "13:00"]]}}
    )
    await book_slot(
        PostgresBookings(db_sessions),
        open_saturday,
        erased_chat,
        SlotRequest("Corte", datetime(2026, 10, 31, 15, 0, tzinfo=UTC), 30, "Laura Prueba"),
        now=datetime(2026, 10, 27, 13, 0, tzinfo=UTC),
    )

    await erase_conversations(db_sessions, seller.id, laura.id, number=LAURA)

    async with db_sessions() as session:
        rows = (
            (
                await session.execute(
                    sa.select(messages).where(messages.c.conversation_id == erased_chat)
                )
            )
            .mappings()
            .all()
        )
        kept = (
            (
                await session.execute(
                    sa.select(messages).where(messages.c.conversation_id == kept_chat)
                )
            )
            .mappings()
            .all()
        )
        bodies = dict(
            (await session.execute(sa.select(webhook_payloads.c.id, webhook_payloads.c.body))).all()
        )
        summary = await session.scalar(
            sa.select(handoffs.c.summary).where(handoffs.c.conversation_id == erased_chat)
        )
        quote = await session.scalar(
            sa.select(objections.c.quote_redacted).where(
                objections.c.conversation_id == erased_chat
            )
        )
        name = await session.scalar(
            sa.select(bookings.c.customer_name).where(bookings.c.conversation_id == erased_chat)
        )
        still_there = await session.scalar(
            sa.select(sa.func.count())
            .select_from(conversations)
            .where(conversations.c.id == erased_chat)
        )

    assert len(rows) == 2  # the rows stay for the counts
    for row in rows:
        assert (row["text"], row["transcript"], row["media_key"]) == (None, None, None)
        assert (row["normalized"], row["agent_output"], row["validation"]) == (None, None, None)
    assert [row["text"] for row in kept] == [
        "¿cuánto vale el envío a Medellín?",
        "El envío dentro de Medellín vale $8.000.",
    ]
    assert bodies[erased_payload] == {}
    assert bodies[status_payload] == {}  # a delivery status to Laura carries her number
    assert bodies[kept_payload] != {}
    assert (summary, quote, name, still_there) == (None, None, None, 1)
