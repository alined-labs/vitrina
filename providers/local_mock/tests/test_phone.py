from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vitrina_core.providers import Blob, Messenger, OutboundImage, OutboundTemplate, OutboundText
from vitrina_local_mock import LocalMockMessenger, MockInbound, MockOutbox, create_phone_router

CUSTOMER = "+573000000001"
OTHER = "+573000000002"


async def test_mock_messenger_keeps_outbound_messages_and_reports_mocked(tmp_path: Path) -> None:
    outbox = MockOutbox(tmp_path / "outbox.jsonl")
    messenger: Messenger = LocalMockMessenger(outbox)

    text = await messenger.send(OutboundText(to=CUSTOMER, text="Hola, ¿en qué te ayudo?"))
    image = await messenger.send(
        OutboundImage(
            to=CUSTOMER, image=Blob(data=b"jpeg", mime_type="image/jpeg"), caption="Bolso #A7"
        )
    )
    template = await messenger.send(OutboundTemplate(to=OTHER, template_name="promo_es"))

    assert {text.status, image.status, template.status} == {"mocked"}
    assert text.message_id != image.message_id
    thread = outbox.entries(contact=CUSTOMER)
    assert [entry.kind for entry in thread] == ["text", "image"]
    assert thread[1].image_data_uri == "data:image/jpeg;base64,anBlZw=="
    assert [entry.template_name for entry in outbox.entries(contact=OTHER)] == ["promo_es"]


def test_outbox_survives_a_second_process(tmp_path: Path) -> None:
    first = MockOutbox(tmp_path / "outbox.jsonl")
    first.record_inbound(CUSTOMER, "Hola, vi el anuncio #A7")

    second = MockOutbox(tmp_path / "outbox.jsonl")

    assert [entry.text for entry in second.entries(contact=CUSTOMER)] == ["Hola, vi el anuncio #A7"]


def test_phone_view_shows_threads_and_forwards_inbound_messages(tmp_path: Path) -> None:
    outbox = MockOutbox(tmp_path / "outbox.jsonl")
    received: list[MockInbound] = []

    async def on_inbound(message: MockInbound) -> None:
        received.append(message)

    app = FastAPI()
    app.include_router(create_phone_router(outbox, on_inbound))
    client = TestClient(app)

    page = client.get("/mock/whatsapp")
    sent = client.post(
        "/mock/whatsapp/inbound", json={"contact": CUSTOMER, "text": "¿Tienen talla M?"}
    )
    thread = client.get("/mock/whatsapp/messages", params={"contact": CUSTOMER})

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Escribe un mensaje" in page.text
    assert sent.status_code == 202
    assert [message.text for message in received] == ["¿Tienen talla M?"]
    assert [(entry["direction"], entry["text"]) for entry in thread.json()] == [
        ("inbound", "¿Tienen talla M?")
    ]


def test_phone_view_rejects_numbers_outside_the_synthetic_range(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_phone_router(MockOutbox(tmp_path / "outbox.jsonl")))

    response = TestClient(app).post(
        "/mock/whatsapp/inbound", json={"contact": "+15555550100", "text": "Hola"}
    )

    assert response.status_code == 422


def test_phone_view_stays_out_of_the_openapi_document(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_phone_router(MockOutbox(tmp_path / "outbox.jsonl")))

    assert app.openapi()["paths"] == {}
