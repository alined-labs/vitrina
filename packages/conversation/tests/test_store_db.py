"""The Postgres conversation store on a migrated schema (data-model.md §1; AS-3.1, FR-028).

Runs where DATABASE_URL points at a Postgres with pgvector: CI's db job. Each write and the
events it reports commit together; every query is scoped to the seller (Art. V.3).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_conversation.api import Attribution, PostgresConversationStore
from vitrina_conversation.tables import handoffs, messages, objections
from vitrina_conversation.tools import HandoffAction, ObjectionAction
from vitrina_core.db.tables import events
from vitrina_core.ids import new_id

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


@dataclass
class Ctx:
    sessions: async_sessionmaker[AsyncSession]
    store: PostgresConversationStore
    seller_id: UUID
    contact_id: UUID


@pytest.fixture
async def ctx(db_sessions: async_sessionmaker[AsyncSession]) -> Ctx:
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
        pepper=SecretStr("store-db-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    contact = await PostgresCompliance(db_sessions, identity).resolve_contact(
        seller.id, "573000000001"
    )
    return Ctx(db_sessions, PostgresConversationStore(db_sessions), seller.id, contact.id)


async def inbound(
    ctx: Ctx, conversation_id: UUID, wa_message_id: str, text: str = "Hola"
) -> object:
    return await ctx.store.add_message(
        ctx.seller_id,
        conversation_id,
        direction="inbound",
        sender="customer",
        type="text",
        text=text,
        wa_message_id=wa_message_id,
        normalized={"kind": "text", "text": text},
    )


async def test_db_as_3_1_one_row_per_whatsapp_message(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)

    first = await inbound(ctx, conversation.id, "wamid.VITRINA-DB-IN-0001")
    duplicate = await inbound(ctx, conversation.id, "wamid.VITRINA-DB-IN-0001")

    assert first is not None
    assert duplicate is None
    again = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    assert again.id == conversation.id


async def test_db_fr_024_recent_messages_come_oldest_first(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    await inbound(ctx, conversation.id, "wamid.VITRINA-DB-IN-0002", "Hola")
    await ctx.store.add_message(
        ctx.seller_id,
        conversation.id,
        direction="outbound",
        sender="agent",
        type="text",
        text="¡Hola! ¿Te aviso de las promos?",
        agent_output={"reply": "¡Hola!", "proposed_consent_prompt": True},
        delivery_status="queued",
    )
    await inbound(ctx, conversation.id, "wamid.VITRINA-DB-IN-0003", "Sí")

    recent = await ctx.store.recent_messages(ctx.seller_id, conversation.id, limit=2)

    assert [message.text for message in recent] == ["¡Hola! ¿Te aviso de las promos?", "Sí"]
    assert recent[0].proposed_consent
    assert await ctx.store.recent_messages(uuid4(), conversation.id, limit=5) == []


async def test_db_fr_028_delivery_status_never_moves_backwards(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    out = await ctx.store.add_message(
        ctx.seller_id,
        conversation.id,
        direction="outbound",
        sender="agent",
        type="text",
        text="¡Hola!",
        delivery_status="queued",
    )
    assert out is not None
    await ctx.store.set_delivery(
        ctx.seller_id, out.id, status="sent", wa_message_id="wamid.VITRINA-DB-OUT-0001"
    )

    assert await ctx.store.apply_status(ctx.seller_id, "wamid.VITRINA-DB-OUT-0001", "read")
    assert await ctx.store.apply_status(ctx.seller_id, "wamid.VITRINA-DB-OUT-0001", "delivered")
    assert not await ctx.store.apply_status(ctx.seller_id, "wamid.VITRINA-DB-UNKNOWN", "read")
    assert not await ctx.store.apply_status(uuid4(), "wamid.VITRINA-DB-OUT-0001", "failed")

    async with ctx.sessions() as session:
        status = await session.scalar(
            sa.select(messages.c.delivery_status).where(messages.c.id == out.id)
        )
    assert status == "read"


async def test_db_as_3_8_a_handoff_and_its_event_commit_together(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    handoff = HandoffAction(new_id(), "customer_request", "Quiere hablar con Ana.")
    payload = {"conversation_id": str(conversation.id), "handoff_id": str(handoff.id)}

    await ctx.store.open_handoff(
        ctx.seller_id, conversation.id, handoff, events=[("ad.handoff", payload)]
    )

    assert (await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)).mode == "human"
    async with ctx.sessions() as session:
        reason = await session.scalar(
            sa.select(handoffs.c.reason).where(handoffs.c.id == handoff.id)
        )
        emitted = (
            await session.execute(
                sa.select(events.c.seller_id, events.c.payload).where(events.c.type == "ad.handoff")
            )
        ).all()
    assert reason == "customer_request"
    assert [(row.seller_id, row.payload) for row in emitted] == [(ctx.seller_id, payload)]


async def test_db_as_4_2_an_objection_is_stored_with_its_quote_redacted(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    objection = ObjectionAction(new_id(), "shipping", "muy caro, llámame al +57 300 000 0001")

    await ctx.store.add_objection(
        ctx.seller_id,
        conversation.id,
        objection,
        ad_id=None,
        events=[("objection.recorded", {"objection_id": str(objection.id), "tag": "shipping"})],
    )

    async with ctx.sessions() as session:
        quote = await session.scalar(
            sa.select(objections.c.quote_redacted).where(objections.c.id == objection.id)
        )
    assert quote == "muy caro, llámame al [número]"


async def test_db_r4_streak_and_attribution_are_kept(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)

    await ctx.store.update_conversation(
        ctx.seller_id,
        conversation.id,
        touched_at=NOW,
        low_confidence_streak=1,
        attribution=Attribution("unknown_code", None, "A9"),
    )

    reopened = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    assert (reopened.low_confidence_streak, reopened.attribution.status) == (1, "unknown_code")


async def test_db_as_3_1_the_raw_payload_is_kept(ctx: Ctx) -> None:
    body = {"object": "whatsapp_business_account", "entry": []}

    payload_id = await ctx.store.save_webhook(body, signature_valid=True)

    assert await ctx.store.webhook(payload_id) == body


async def test_db_as_3_8_the_waiting_list_and_its_handoff(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    handoff = HandoffAction(new_id(), "customer_request", "Quiere hablar con Ana.")
    await ctx.store.open_handoff(ctx.seller_id, conversation.id, handoff)

    waiting, _ = await ctx.store.list_conversations(ctx.seller_id, limit=10, needs_attention=True)
    await ctx.store.add_message(
        ctx.seller_id,
        conversation.id,
        direction="outbound",
        sender="seller",
        type="text",
        text="Hola, soy Ana.",
        delivery_status="sent",
    )
    after, _ = await ctx.store.list_conversations(ctx.seller_id, limit=10, needs_attention=True)

    (record,) = waiting
    assert record.handoff is not None
    assert (record.handoff.id, record.handoff.reason) == (handoff.id, "customer_request")
    assert after == []


async def test_db_as_3_9_release_closes_the_handoff_and_resets_the_streak(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    await ctx.store.update_conversation(
        ctx.seller_id, conversation.id, touched_at=NOW, low_confidence_streak=2
    )
    await ctx.store.open_handoff(
        ctx.seller_id, conversation.id, HandoffAction(new_id(), "low_confidence_twice", "No sé.")
    )

    await ctx.store.set_mode(ctx.seller_id, conversation.id, "agent", touched_at=NOW)

    record = await ctx.store.get_conversation(ctx.seller_id, conversation.id)
    assert record is not None
    assert (record.state.mode, record.state.low_confidence_streak) == ("agent", 0)
    assert record.handoff is not None
    assert record.handoff.released_at == NOW
    assert await ctx.store.get_conversation(uuid4(), conversation.id) is None


async def test_db_fr_080_messages_page_oldest_first_with_a_cursor(ctx: Ctx) -> None:
    conversation = await ctx.store.open_conversation(ctx.seller_id, ctx.contact_id)
    for n in range(3):
        await inbound(ctx, conversation.id, f"wamid.VITRINA-DB-PAGE-{n}", f"mensaje {n}")

    first, cursor = await ctx.store.list_messages(ctx.seller_id, conversation.id, limit=2)
    rest, end = await ctx.store.list_messages(
        ctx.seller_id, conversation.id, limit=2, cursor=cursor
    )

    assert [m.text for m in first] == ["mensaje 0", "mensaje 1"]
    assert [m.text for m in rest] == ["mensaje 2"]
    assert (cursor is not None, end) == (True, None)
