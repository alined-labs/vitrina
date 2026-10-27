"""The in-memory conversation store (data-model.md §1; AS-3.1, FR-028, Art. V.3)."""

from uuid import uuid4

import pytest

from vitrina_conversation.api import InMemoryConversationStore
from vitrina_conversation.store import redact
from vitrina_conversation.tools import ObjectionAction
from vitrina_core.ids import new_id

SELLER, CONTACT = uuid4(), uuid4()


async def test_as_3_1_the_same_whatsapp_message_is_stored_once() -> None:
    store = InMemoryConversationStore()
    conversation = await store.open_conversation(SELLER, CONTACT)

    stored = [
        await store.add_message(
            SELLER,
            conversation.id,
            direction="inbound",
            sender="customer",
            type="text",
            text="Hola",
            wa_message_id="wamid.VITRINA-MEM-0001",
        )
        for _ in range(2)
    ]

    assert stored[0] is not None
    assert stored[1] is None


async def test_fr_028_status_callbacks_never_move_a_message_backwards() -> None:
    store = InMemoryConversationStore()
    conversation = await store.open_conversation(SELLER, CONTACT)
    out = await store.add_message(
        SELLER,
        conversation.id,
        direction="outbound",
        sender="agent",
        type="text",
        text="¡Hola!",
        delivery_status="queued",
    )
    assert out is not None
    await store.set_delivery(SELLER, out.id, status="sent", wa_message_id="wamid.VITRINA-MEM-OUT")

    await store.apply_status(SELLER, "wamid.VITRINA-MEM-OUT", "read")
    await store.apply_status(SELLER, "wamid.VITRINA-MEM-OUT", "delivered")

    assert store.messages[-1].stored.delivery_status == "read"
    assert not await store.apply_status(uuid4(), "wamid.VITRINA-MEM-OUT", "failed")


async def test_art_v_3_a_conversation_belongs_to_its_seller() -> None:
    store = InMemoryConversationStore()
    conversation = await store.open_conversation(SELLER, CONTACT)

    assert await store.recent_messages(uuid4(), conversation.id, limit=5) == []
    with pytest.raises(LookupError):
        await store.add_objection(
            uuid4(), conversation.id, ObjectionAction(new_id(), "price", "caro"), ad_id=None
        )


@pytest.mark.parametrize(
    ("quote", "redacted"),
    [
        ("llámame al +57 300 000 0001", "llámame al [número]"),
        ("escríbeme a laura@correo.example", "escríbeme a [correo]"),
        ("muy caro el envío", "muy caro el envío"),
        ("talla 38 por favor", "talla 38 por favor"),
    ],
)
def test_as_4_2_objection_quotes_lose_numbers_and_emails(quote: str, redacted: str) -> None:
    assert redact(quote) == redacted
