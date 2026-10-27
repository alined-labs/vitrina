"""FR-020, FR-021, AS-3.1: the WhatsApp webhook routes, end to end without a database.

Without DATABASE_URL the API answers each message in a background task after the ack, through
the same pipeline the worker runs, and the local mock messenger takes the reply (ADR-0011).
"""

import dataclasses
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.sellers import SellerCreate
from vitrina_conversation.testing import ScriptedChat, final_answer, webhook_fixture
from vitrina_core.settings import Settings
from vitrina_local_mock import MockOutbox
from vitrina_whatsapp.signature import sign

APP_SECRET = "webhook-routes-test-app-secret"
VERIFY_TOKEN = "webhook-routes-test-verify-token"
SELLER = UUID("00000000-0000-7000-8000-0000000000b1")
CUSTOMER = "+573000000001"
GREETING = "¡Hola! ¿En qué te ayudo?"


@dataclasses.dataclass
class Hook:
    client: TestClient
    app: Any
    outbox: MockOutbox
    chat: ScriptedChat

    def post(self, body: bytes, signature: str | None) -> Any:
        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return self.client.post("/v1/webhooks/whatsapp", content=body, headers=headers)

    def replies(self) -> list[str | None]:
        return [entry.text for entry in self.outbox.entries(contact=CUSTOMER)]


@pytest.fixture
async def hook(tmp_path: Path) -> Hook:
    outbox_path = tmp_path / "mock-whatsapp.jsonl"
    app = create_app(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            whatsapp_app_secret=APP_SECRET,
            whatsapp_verify_token=VERIFY_TOKEN,
            model_agent="spark-test",
            mock_outbox_path=outbox_path,
            contact_hash_pepper="webhook-routes-test-pepper",
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
    return Hook(TestClient(app), app, MockOutbox(outbox_path), chat)


def test_fr_020_the_handshake_echoes_the_challenge(hook: Hook) -> None:
    response = hook.client.get(
        "/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


def test_fr_020_a_wrong_verify_token_is_forbidden(hook: Hook) -> None:
    response = hook.client.get(
        "/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_as_3_1_a_signed_message_is_acknowledged_and_answered(hook: Hook) -> None:
    hook.chat.script.append(final_answer(GREETING, confidence=0.95))
    body = webhook_fixture("text_ad_code_a7")

    started = time.perf_counter()
    response = hook.post(body, sign(body, APP_SECRET))
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert response.content == b""
    assert elapsed < 2.0
    assert hook.replies() == [GREETING]


@pytest.mark.parametrize("signature", [None, "sha256=" + "0" * 64])
def test_as_3_1_an_invalid_signature_is_401_and_nothing_runs(
    hook: Hook, signature: str | None
) -> None:
    response = hook.post(webhook_fixture("text_ad_code_a7"), signature)

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_SIGNATURE"
    assert hook.replies() == []
    assert hook.chat.requests == []


def test_as_3_1_a_redelivered_message_is_answered_once(hook: Hook) -> None:
    hook.chat.script.append(final_answer(GREETING, confidence=0.95))
    body = webhook_fixture("text_ad_code_a7")

    first = hook.post(body, sign(body, APP_SECRET))
    second = hook.post(body, sign(body, APP_SECRET))

    assert (first.status_code, second.status_code) == (200, 200)
    assert hook.replies() == [GREETING]


def test_fr_028_a_status_callback_is_acknowledged(hook: Hook) -> None:
    body = webhook_fixture("status_delivered")

    assert hook.post(body, sign(body, APP_SECRET)).status_code == 200


def test_as_3_1_a_payload_this_api_cannot_read_is_still_acknowledged(hook: Hook) -> None:
    body = b'{"object": "page", "entry": []}'

    assert hook.post(body, sign(body, APP_SECRET)).status_code == 200
    assert hook.replies() == []
