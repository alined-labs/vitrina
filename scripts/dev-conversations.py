"""A local API with sample chats, to work on the conversation screen without a database.

    uv run python scripts/dev-conversations.py [--port 8000]
    uv run python scripts/dev-conversations.py customer "¿Tienen envíos?" [--port 8000]
    uv run python scripts/dev-conversations.py status <wa_message_id> failed [--port 8000]

``serve``, the default, runs the real API routes on in-memory stores, replay providers and the
local WhatsApp mock. Its settings are fixed here: it reads no ``.env``, so it cannot reach a
hosted service or a paid model. ``customer`` and ``status`` post a webhook signed as Meta would,
so a message or a delivery update arrives while the page is open.

Sends to the customer in "Rechazo de envío" raise a provider error, as a refused send would.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import sys
import time
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import SecretStr

from vitrina_api.main import create_app
from vitrina_catalog.schemas import FactCreate, ProductCreate
from vitrina_catalog.sellers import SellerCreate
from vitrina_conversation.api import HandoffAction, conversation_store, messenger
from vitrina_conversation.attribution import Attribution
from vitrina_conversation.store import InMemoryConversationStore
from vitrina_core.providers import ProviderError
from vitrina_core.providers.messaging import Messenger, OutboundMessage, SendResult
from vitrina_core.settings import Settings

# Local placeholders, valid only against this process. Synthetic numbers (Art. V).
JWT_SECRET = "local-only-conversation-screen-placeholder"
APP_SECRET = "local-only-webhook-placeholder"
SUBJECT = "dev-seller"
SHOP_NUMBER = "573000000000"
AGENT_CHAT = "573000000101"
WAITING_CHAT = "573000000102"
OLD_CHAT = "573000000103"
LONG_CHAT = "573000000104"
CLOSED_CHAT = "573000000105"
REFUSED_CHAT = "573000000106"
COUNTERS = iter(range(1, 100_000))


class RefusingMessenger:
    """The local mock, except that one customer's sends fail."""

    def __init__(self, inner: Messenger) -> None:
        self.inner = inner

    async def send(self, message: OutboundMessage) -> SendResult:
        if message.to.removeprefix("+") == REFUSED_CHAT:
            raise ProviderError("refused for this local sample", provider="local_mock")
        return await self.inner.send(message)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        replay=True,
        record_cassettes=False,
        database_url=None,
        db_schema=None,
        supabase_url=None,
        supabase_jwt_secret=SecretStr(JWT_SECRET),
        supabase_service_role_key=None,
        storage_backend="local",
        whatsapp_mock=True,
        whatsapp_app_secret=SecretStr(APP_SECRET),
        contact_hash_pepper=SecretStr("local-only-pepper-placeholder"),
        contact_encryption_key=SecretStr(Fernet.generate_key().decode()),
        meta_api_key=None,
        baseten_api_key=None,
        model_agent="muse-spark-1.3",
    )


def _ticks(start: datetime, step: timedelta) -> Iterator[datetime]:
    moment = start
    while True:
        yield moment
        moment += step


async def _seed(app: FastAPI) -> dict[str, UUID]:
    state = app.state
    store = conversation_store(state)
    assert isinstance(store, InMemoryConversationStore)
    state.messenger = RefusingMessenger(messenger(state))
    seller = await state.sellers.create(
        SellerCreate(
            business_name="Tienda de prueba",
            owner_name="Dueña de prueba",
            whatsapp_number=f"+{SHOP_NUMBER}",
            city="Bogotá",
        ),
        auth_subject=SUBJECT,
    )
    seller_id = seller.id
    bag = await state.catalog.create_product(
        seller_id, ProductCreate(name="Mochila de prueba", price_cop=85000, stock=4)
    )
    shipping = await state.catalog.create_fact(
        seller_id, FactCreate(kind="shipping", text="Envío a Medellín por $ 12.000, en 2 días.")
    )
    now = datetime.now(UTC)

    async def chat(wa_id: str, name: str, start: timedelta) -> tuple[UUID, Iterator[datetime]]:
        contact = await state.compliance.resolve_contact(seller_id, wa_id, display_name=name)
        clock = _ticks(now - start, timedelta(minutes=2))
        store.clock = lambda: next(clock)
        conversation = await store.open_conversation(seller_id, contact.id)
        return conversation.id, clock

    async def says(conversation_id: UUID, sender: str, text: str, **extra: Any) -> None:
        inbound = sender == "customer"
        await store.add_message(
            seller_id,
            conversation_id,
            direction="inbound" if inbound else "outbound",
            sender=sender,  # type: ignore[arg-type]
            type=extra.pop("type", "text"),
            text=text,
            wa_message_id=f"wamid.LOCAL-{next(COUNTERS):04d}",
            **({} if inbound else {"delivery_status": extra.pop("status", "read")}),
            **extra,
        )
        await store.update_conversation(seller_id, conversation_id, touched_at=store.clock())

    def answer(reply: str, *cited: UUID) -> dict[str, Any]:
        return {
            "reply": reply,
            "cited_fact_ids": [str(item) for item in cited],
            "confidence": 0.93,
            "proposed_consent_prompt": False,
        }

    # The agent has it: citations on its replies, and one reply the validators replaced.
    agent, _ = await chat(AGENT_CHAT, "Cliente con el agente", timedelta(minutes=30))
    await store.update_conversation(
        seller_id,
        agent,
        touched_at=now,
        attribution=Attribution("attributed", ad_id=uuid4(), code="A7"),
    )
    await says(agent, "customer", "Hola, vi el anuncio #A7. ¿Cuánto vale la mochila?")
    reply = "La mochila de prueba vale $ 85.000 y nos quedan 4."
    await says(agent, "agent", reply, agent_output=answer(reply, bag.id))
    await says(agent, "customer", "¿Y el envío a Medellín?")
    reply = "El envío a Medellín cuesta $ 12.000 y tarda 2 días."
    await says(agent, "agent", reply, agent_output=answer(reply, shipping.id), status="delivered")
    await says(agent, "customer", "¿Me la dejan en 50 mil?")
    await says(
        agent,
        "agent",
        "No tengo ese dato. Le pregunto a la dueña y te responde.",
        agent_output=answer("Claro, te la dejo en $ 50.000.", bag.id),
        status="sent",
    )

    # Handed to the seller, inside the 24-hour window.
    waiting, _ = await chat(WAITING_CHAT, "Cliente que te espera", timedelta(minutes=20))
    await says(waiting, "customer", "Quiero hablar con la dueña, por favor.")
    await store.open_handoff(
        seller_id,
        waiting,
        HandoffAction(uuid4(), "customer_request", "Pidió hablar contigo sobre un pedido grande."),
    )

    # Handed to the seller, with the window long closed and one reply that already failed.
    old, _ = await chat(OLD_CHAT, "Cliente de hace dos días", timedelta(hours=50))
    await says(old, "customer", "Gracias, lo pienso y te aviso.")
    await store.open_handoff(
        seller_id, old, HandoffAction(uuid4(), "other", "Quedó pensando la compra.")
    )
    store.clock = lambda: now - timedelta(hours=20)
    await says(old, "seller", "Hola, ¿ya lo pensaste?", status="failed")

    # More messages than one page, to follow next_cursor.
    long, _ = await chat(LONG_CHAT, "Cliente de chat largo", timedelta(hours=9))
    for number in range(1, 231):
        sender = "customer" if number % 2 else "agent"
        await says(long, sender, f"Mensaje número {number} de este chat largo.")

    # Closed: the page offers no reply.
    closed, _ = await chat(CLOSED_CHAT, "Cliente de chat cerrado", timedelta(hours=3))
    await says(closed, "customer", "Eso era todo, gracias.")
    await store.set_mode(seller_id, closed, "closed", touched_at=now)

    # Every send to this customer fails, and the API still answers 201.
    refused, _ = await chat(REFUSED_CHAT, "Rechazo de envío", timedelta(minutes=10))
    await says(refused, "customer", "¿Me confirmas el pedido?")
    await store.open_handoff(
        seller_id, refused, HandoffAction(uuid4(), "other", "Espera confirmación del pedido.")
    )

    store.clock = lambda: datetime.now(UTC)
    return {
        "agent": agent,
        "waiting": waiting,
        "old": old,
        "long": long,
        "closed": closed,
        "refused": refused,
    }


def _serve(port: int) -> None:
    settings = _settings()
    app = create_app(settings)
    chats = asyncio.run(_seed(app))
    token = f"SUPABASE_URL= SUPABASE_JWT_SECRET={JWT_SECRET} make -s dev-token SUB={SUBJECT}"
    print(f"Token for the dashboard:\n  {token}\n\nSample chats:")
    for name, chat_id in chats.items():
        print(f"  {name:8} /conversaciones/{chat_id}")
    sys.stdout.flush()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _post(port: int, value: dict[str, Any]) -> None:
    metadata = {"display_phone_number": SHOP_NUMBER, "phone_number_id": "100000000000001"}
    change = {"messaging_product": "whatsapp", "metadata": metadata, **value}
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {"id": "100000000000002", "changes": [{"value": change, "field": "messages"}]}
            ],
        }
    ).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/webhooks/whatsapp",
        data=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        print(response.status)


def _customer(port: int, text: str, wa_id: str) -> None:
    message = {
        "from": wa_id,
        "id": f"wamid.LOCAL-IN-{uuid4().hex}",
        "timestamp": str(int(time.time())),
        "type": "text",
        "text": {"body": text},
    }
    contact = {"profile": {"name": "Cliente de prueba"}, "wa_id": wa_id}
    _post(port, {"contacts": [contact], "messages": [message]})


def _status(port: int, wa_message_id: str, status: str) -> None:
    update: dict[str, Any] = {
        "id": wa_message_id,
        "status": status,
        "timestamp": str(int(time.time())),
        "recipient_id": AGENT_CHAT,
    }
    if status == "failed":
        update["errors"] = [{"code": 131047, "title": "Re-engagement message"}]
    _post(port, {"statuses": [update]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    commands = parser.add_subparsers(dest="command")
    customer = commands.add_parser("customer", help="a customer writes")
    customer.add_argument("text")
    customer.add_argument("--wa-id", default=WAITING_CHAT, help="which sample customer writes")
    customer.add_argument("--port", type=int, default=8000)
    status = commands.add_parser("status", help="WhatsApp reports a delivery update")
    status.add_argument("wa_message_id")
    status.add_argument("status", choices=["sent", "delivered", "read", "failed"])
    status.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "customer":
        _customer(args.port, args.text, args.wa_id)
    elif args.command == "status":
        _status(args.port, args.wa_message_id, args.status)
    else:
        _serve(args.port)


if __name__ == "__main__":
    main()
