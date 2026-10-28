"""Conversations router: the seller's view of chats, takeover and release (AS-3.8, AS-3.9).

No database: conversations come in through the signed webhook and the pipeline, as in
production; the local mock messenger delivers every reply.
"""

import dataclasses
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.sellers import SellerCreate
from vitrina_conversation.testing import ScriptedChat, final_answer, webhook_fixture
from vitrina_core.settings import Settings
from vitrina_local_mock import MockOutbox
from vitrina_whatsapp.signature import sign

JWT_SECRET = "conversations-routes-test-secret-32b"
APP_SECRET = "conversations-routes-app-secret"
SELLER = UUID("00000000-0000-7000-8000-0000000000c1")
CUSTOMER = "+573000000001"
GREETING = "¡Hola! ¿En qué te ayudo?"


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller_id)}
    return {"Authorization": f"Bearer {jwt.encode(claims, JWT_SECRET, algorithm='HS256')}"}


@dataclasses.dataclass
class Shop:
    client: TestClient
    chat: ScriptedChat
    outbox: MockOutbox

    def customer_says(self, fixture: str = "text_ad_code_a7", **replace: str) -> None:
        body = webhook_fixture(fixture)
        for old, new in replace.items():
            body = body.replace(old.encode(), new.encode())
        response = self.client.post(
            "/v1/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sign(body, APP_SECRET),
            },
        )
        assert response.status_code == 200

    def get(self, path: str, **params: Any) -> Any:
        return self.client.get(f"/v1{path}", params=params, headers=bearer())

    def post(self, path: str, json: Any = None, seller_id: UUID = SELLER) -> Any:
        return self.client.post(f"/v1{path}", json=json, headers=bearer(seller_id))

    def only_conversation(self) -> dict[str, Any]:
        (conversation,) = self.get("/conversations").json()["items"]
        return dict(conversation)


@pytest.fixture
async def shop(tmp_path: Path) -> Shop:
    outbox_path = tmp_path / "mock-whatsapp.jsonl"
    app = create_app(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            supabase_jwt_secret=JWT_SECRET,
            whatsapp_app_secret=APP_SECRET,
            model_agent="spark-test",
            mock_outbox_path=outbox_path,
            contact_hash_pepper="conversations-routes-pepper",
            contact_encryption_key=Fernet.generate_key().decode(),
        )
    )
    await app.state.sellers.create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000000",
            city="Medellín",
        ),
        auth_subject=None,
        seller_id=SELLER,
    )
    chat = ScriptedChat()
    app.state.providers = dataclasses.replace(app.state.providers, chat=chat)
    return Shop(TestClient(app), chat, MockOutbox(outbox_path))


def test_a_chat_shows_up_with_its_messages(shop: Shop) -> None:
    shop.chat.script.append(final_answer(GREETING, confidence=0.95))
    shop.customer_says()

    conversation = shop.only_conversation()
    messages = shop.get(f"/conversations/{conversation['id']}/messages").json()

    assert (conversation["mode"], conversation["attribution_status"]) == ("agent", "unknown_code")
    assert conversation["attributed_ad_code"] is None
    assert conversation["handoff"] is None
    assert [(m["direction"], m["sender"], m["text"]) for m in messages["items"]] == [
        ("inbound", "customer", "Hola, vi el anuncio #A7"),
        ("outbound", "agent", GREETING),
    ]
    inbound, outbound = messages["items"]
    assert inbound["normalized"]["ad_code"] == "A7"
    assert outbound["agent_output"]["reply"] == GREETING
    assert outbound["delivery_status"] == "mocked"
    assert messages["next_cursor"] is None


def test_a_conversation_is_found_only_in_its_own_shop(shop: Shop) -> None:
    shop.chat.script.append(final_answer(GREETING, confidence=0.95))
    shop.customer_says()
    conversation_id = shop.only_conversation()["id"]
    other = SELLER.int + 1

    mine = shop.get(f"/conversations/{conversation_id}")
    theirs = shop.client.get(
        f"/v1/conversations/{conversation_id}", headers=bearer(UUID(int=other))
    )

    assert mine.status_code == 200
    assert theirs.status_code == 404
    assert shop.get(f"/conversations/{uuid4()}").json()["code"] == "NOT_FOUND"


def test_as_3_8_a_handed_off_chat_waits_for_the_seller(shop: Shop) -> None:
    shop.chat.script.append(
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")
    )
    shop.customer_says()

    waiting = shop.get("/conversations", needs_attention=True).json()["items"]
    agent_mode = shop.get("/conversations", mode="agent").json()["items"]

    (conversation,) = waiting
    assert agent_mode == []
    assert conversation["mode"] == "human"
    assert conversation["handoff"]["reason"] == "customer_request"
    assert conversation["handoff"]["released_at"] is None


def test_a_seller_message_needs_human_mode(shop: Shop) -> None:
    shop.chat.script.append(final_answer(GREETING, confidence=0.95))
    shop.customer_says()
    conversation_id = shop.only_conversation()["id"]

    response = shop.post(f"/conversations/{conversation_id}/messages", {"text": "Hola, soy Ana"})

    assert response.status_code == 409
    assert response.json()["code"] == "CONVERSATION_NOT_HUMAN"


def test_takeover_lets_the_seller_write_and_clears_the_waiting_list(shop: Shop) -> None:
    shop.chat.script.append(
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")
    )
    shop.customer_says()
    conversation_id = shop.only_conversation()["id"]

    taken = shop.post(f"/conversations/{conversation_id}/takeover")
    sent = shop.post(
        f"/conversations/{conversation_id}/messages", {"text": "Hola, soy Ana. ¿Qué necesitas?"}
    )

    assert taken.status_code == 200
    assert taken.json()["mode"] == "human"
    assert sent.status_code == 201
    message = sent.json()
    assert (message["direction"], message["sender"], message["delivery_status"]) == (
        "outbound",
        "seller",
        "mocked",
    )
    assert "Hola, soy Ana. ¿Qué necesitas?" in [
        e.text for e in shop.outbox.entries(contact=CUSTOMER)
    ]
    assert shop.get("/conversations", needs_attention=True).json()["items"] == []


def test_as_3_9_release_returns_the_chat_to_the_agent(shop: Shop) -> None:
    shop.chat.script.extend(
        [
            final_answer(
                "Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request"
            ),
            final_answer(GREETING, confidence=0.95),
        ]
    )
    shop.customer_says()
    conversation_id = shop.only_conversation()["id"]

    released = shop.post(f"/conversations/{conversation_id}/release")
    shop.customer_says(**{"wamid.VITRINA-FIXTURE-IN-0001": "wamid.VITRINA-FIXTURE-IN-0101"})

    assert released.status_code == 200
    assert released.json()["mode"] == "agent"
    assert released.json()["handoff"]["released_at"] is not None
    replies = [e.text for e in shop.outbox.entries(contact=CUSTOMER)]
    assert replies[-1] == GREETING


def test_conversations_page_with_a_cursor(shop: Shop) -> None:
    shop.chat.script.extend([final_answer(GREETING, confidence=0.95)] * 2)
    shop.customer_says()
    shop.customer_says(
        **{
            "wamid.VITRINA-FIXTURE-IN-0001": "wamid.VITRINA-FIXTURE-IN-0201",
            '"573000000001"': '"573000000002"',
        }
    )

    first = shop.get("/conversations", limit=1).json()
    second = shop.get("/conversations", limit=1, cursor=first["next_cursor"]).json()

    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert second["next_cursor"] is None
