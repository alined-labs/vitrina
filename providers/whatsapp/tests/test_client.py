"""FR-028, AS-3.11: outbound sends through the Cloud API, retried on throttling and server errors."""

import json
from typing import Any

import httpx2
import pytest

from vitrina_core.providers import (
    Blob,
    OutboundImage,
    OutboundMessage,
    OutboundTemplate,
    OutboundText,
    ProviderError,
    SendResult,
    is_retryable,
)
from vitrina_whatsapp.client import WhatsAppClient, WhatsAppSendError
from vitrina_whatsapp.config import WhatsAppConfig

PHONE_NUMBER_ID = "100000000000001"
ACCESS_TOKEN = "fixture-access-token"
CUSTOMER = "573000000001"
MESSAGES_URL = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages"
MEDIA_URL = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/media"
SENT_ID = "wamid.VITRINA-FIXTURE-OUT-0001"
MEDIA_ID = "1000000000000301"

Scripted = httpx2.Response | type[httpx2.TransportError]


def accepted(message_id: str = SENT_ID) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": CUSTOMER, "wa_id": CUSTOMER}],
            "messages": [{"id": message_id}],
        },
    )


def graph_error(
    status: int, code: int, details: str = "fixture details", **headers: str
) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers=headers,
        json={
            "error": {
                "message": f"(#{code}) fixture error",
                "type": "OAuthException",
                "code": code,
                "error_data": {"messaging_product": "whatsapp", "details": details},
                "fbtrace_id": "FIXTURE-TRACE",
            }
        },
    )


class ScriptedGraph:
    """Answers each request with the next scripted response (or transport failure)."""

    def __init__(self, *script: Scripted) -> None:
        self.script = list(script)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        outcome = self.script.pop(0)
        if isinstance(outcome, httpx2.Response):
            return outcome
        raise outcome("scripted transport failure", request=request)

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


class Clock:
    """Records backoff sleeps instead of waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def client_for(graph: ScriptedGraph, clock: Clock) -> WhatsAppClient:
    return WhatsAppClient(
        access_token=ACCESS_TOKEN,
        phone_number_id=PHONE_NUMBER_ID,
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(graph)),
        sleep=clock.sleep,
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


async def test_fr_028_send_text_posts_a_cloud_api_text_message(clock: Clock) -> None:
    graph = ScriptedGraph(accepted())

    async with client_for(graph, clock) as client:
        result = await client.send_text("+573000000001", "Sí, lo tenemos en rojo.")

    assert result == SendResult(message_id=SENT_ID, status="sent")
    (request,) = graph.requests
    assert (request.method, str(request.url)) == ("POST", MESSAGES_URL)
    assert request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert graph.bodies == [
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": CUSTOMER,
            "type": "text",
            "text": {"body": "Sí, lo tenemos en rojo.", "preview_url": False},
        }
    ]


async def test_fr_028_reply_quotes_the_customer_message(clock: Clock) -> None:
    graph = ScriptedGraph(accepted())

    async with client_for(graph, clock) as client:
        await client.send_text(CUSTOMER, "¡Claro!", reply_to="wamid.VITRINA-FIXTURE-IN-0002")

    assert graph.bodies[0]["context"] == {"message_id": "wamid.VITRINA-FIXTURE-IN-0002"}


async def test_fr_028_send_image_by_link_with_caption(clock: Clock) -> None:
    graph = ScriptedGraph(accepted())

    async with client_for(graph, clock) as client:
        await client.send_image(
            CUSTOMER, link="https://cdn.vitrina.example/a7.jpg", caption="Bolso rojo #A7"
        )

    (body,) = graph.bodies
    assert (body["type"], body["image"]) == (
        "image",
        {"link": "https://cdn.vitrina.example/a7.jpg", "caption": "Bolso rojo #A7"},
    )


async def test_fr_028_send_image_by_uploaded_media_id(clock: Clock) -> None:
    graph = ScriptedGraph(accepted())

    async with client_for(graph, clock) as client:
        await client.send_image(CUSTOMER, media_id="1000000000000301")

    assert graph.bodies[0]["image"] == {"id": "1000000000000301"}


@pytest.mark.parametrize(
    ("link", "media_id"), [(None, None), ("https://cdn.vitrina.example/a7.jpg", "1000000000000301")]
)
async def test_fr_028_send_image_needs_exactly_one_source(
    link: str | None, media_id: str | None, clock: Clock
) -> None:
    graph = ScriptedGraph()

    async with client_for(graph, clock) as client:
        with pytest.raises(ValueError, match="exactly one"):
            await client.send_image(CUSTOMER, link=link, media_id=media_id)

    assert graph.requests == []


async def test_fr_028_send_template_with_language_and_components(clock: Clock) -> None:
    graph = ScriptedGraph(accepted())
    components = [{"type": "body", "parameters": [{"type": "text", "text": "Laura"}]}]

    async with client_for(graph, clock) as client:
        await client.send_template(CUSTOMER, "vitrina_reactivacion", components=components)

    (body,) = graph.bodies
    assert (body["type"], body["template"]) == (
        "template",
        {"name": "vitrina_reactivacion", "language": {"code": "es"}, "components": components},
    )


@pytest.mark.parametrize("to", ["", "not-a-number", "+57 300 abc"])
async def test_fr_028_recipient_must_be_a_phone_number(to: str, clock: Clock) -> None:
    async with client_for(ScriptedGraph(), clock) as client:
        with pytest.raises(ValueError, match="recipient"):
            await client.send_text(to, "Hola")


async def test_as_3_11_429_is_retried_with_exponential_backoff(clock: Clock) -> None:
    graph = ScriptedGraph(graph_error(429, 130429), graph_error(429, 130429), accepted())

    async with client_for(graph, clock) as client:
        result = await client.send_text(CUSTOMER, "Hola")

    assert result.message_id == SENT_ID
    assert len(graph.requests) == 3
    assert clock.sleeps == [1.0, 2.0]


async def test_as_3_11_gives_up_after_three_attempts(clock: Clock) -> None:
    graph = ScriptedGraph(*[graph_error(429, 130429)] * 3)

    async with client_for(graph, clock) as client:
        with pytest.raises(WhatsAppSendError) as caught:
            await client.send_text(CUSTOMER, "Hola")

    error = caught.value
    assert (error.status_code, error.code, error.retryable, error.attempts) == (
        429,
        130429,
        True,
        3,
    )
    assert len(graph.requests) == 3
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_as_3_11_server_errors_are_retried(status: int, clock: Clock) -> None:
    graph = ScriptedGraph(httpx2.Response(status), accepted())

    async with client_for(graph, clock) as client:
        await client.send_text(CUSTOMER, "Hola")

    assert len(graph.requests) == 2
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize("code", [4, 80007, 130429, 131056])
async def test_as_3_11_throttling_codes_are_retried_even_as_http_400(
    code: int, clock: Clock
) -> None:
    graph = ScriptedGraph(graph_error(400, code), accepted())

    async with client_for(graph, clock) as client:
        await client.send_text(CUSTOMER, "Hola")

    assert len(graph.requests) == 2


async def test_fr_028_permanent_errors_are_raised_without_retry(clock: Clock) -> None:
    details = (
        "More than 24 hours have passed since the recipient last replied to the sender number."
    )
    graph = ScriptedGraph(graph_error(400, 131047, details))

    async with client_for(graph, clock) as client:
        with pytest.raises(WhatsAppSendError) as caught:
            await client.send_text(CUSTOMER, "Hola")

    error = caught.value
    assert (error.status_code, error.code, error.retryable, error.attempts) == (
        400,
        131047,
        False,
        1,
    )
    assert (error.details, error.fbtrace_id) == (details, "FIXTURE-TRACE")
    assert clock.sleeps == []


async def test_as_3_11_retry_after_header_stretches_the_backoff(clock: Clock) -> None:
    graph = ScriptedGraph(graph_error(429, 130429, **{"Retry-After": "3"}), accepted())

    async with client_for(graph, clock) as client:
        await client.send_text(CUSTOMER, "Hola")

    assert clock.sleeps == [3.0]


async def test_as_3_11_long_retry_after_is_handed_back_to_the_caller(clock: Clock) -> None:
    graph = ScriptedGraph(graph_error(429, 130429, **{"Retry-After": "120"}))

    async with client_for(graph, clock) as client:
        with pytest.raises(WhatsAppSendError) as caught:
            await client.send_text(CUSTOMER, "Hola")

    assert (caught.value.retryable, caught.value.retry_after_s) == (True, 120.0)
    assert len(graph.requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("failure", [httpx2.ConnectError, httpx2.ConnectTimeout])
async def test_as_3_11_requests_that_never_left_are_retried(
    failure: type[httpx2.TransportError], clock: Clock
) -> None:
    graph = ScriptedGraph(failure, accepted())

    async with client_for(graph, clock) as client:
        result = await client.send_text(CUSTOMER, "Hola")

    assert result.message_id == SENT_ID
    assert len(graph.requests) == 2


@pytest.mark.parametrize("failure", [httpx2.ReadTimeout, httpx2.ReadError])
async def test_fr_028_possibly_delivered_send_is_not_retried(
    failure: type[httpx2.TransportError], clock: Clock
) -> None:
    graph = ScriptedGraph(failure, accepted())

    async with client_for(graph, clock) as client:
        with pytest.raises(WhatsAppSendError) as caught:
            await client.send_text(CUSTOMER, "Hola")

    assert not caught.value.retryable
    assert len(graph.requests) == 1


async def test_fr_028_success_without_message_id_is_an_error(clock: Clock) -> None:
    graph = ScriptedGraph(httpx2.Response(200, json={"messaging_product": "whatsapp"}))

    async with client_for(graph, clock) as client:
        with pytest.raises(WhatsAppSendError) as caught:
            await client.send_text(CUSTOMER, "Hola")

    assert not caught.value.retryable


async def test_fr_028_client_is_built_from_config() -> None:
    config = WhatsAppConfig(
        phone_number_id=PHONE_NUMBER_ID,
        app_secret="fixture-app-secret",
        verify_token="fixture-verify-token",
        access_token=ACCESS_TOKEN,
        graph_version="v25.0",
    )

    async with WhatsAppClient.from_config(config) as client:
        assert client.messages_url == (
            f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
        )


def test_fr_028_client_refuses_missing_credentials() -> None:
    with pytest.raises(ValueError, match="access token"):
        WhatsAppClient(access_token="", phone_number_id=PHONE_NUMBER_ID)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (OutboundText(to="+573000000001", text="Hola"), "text"),
        (OutboundTemplate(to="+573000000001", template_name="vitrina_reactivacion"), "template"),
        (
            OutboundImage(
                to="+573000000001", image_url="https://cdn.vitrina.example/a7.jpg", caption="#A7"
            ),
            "image",
        ),
    ],
    ids=["text", "template", "image-url"],
)
async def test_fr_028_messenger_sends_every_outbound_kind(
    message: OutboundMessage, kind: str, clock: Clock
) -> None:
    graph = ScriptedGraph(accepted())

    async with client_for(graph, clock) as client:
        result = await client.send(message)

    assert result == SendResult(message_id=SENT_ID, status="sent")
    (body,) = graph.bodies
    assert (body["to"], body["type"]) == (CUSTOMER, kind)


async def test_fr_028_messenger_uploads_image_bytes_then_sends_by_id(clock: Clock) -> None:
    graph = ScriptedGraph(httpx2.Response(200, json={"id": MEDIA_ID}), accepted())
    image = Blob(data=b"\x89PNG fixture bytes", mime_type="image/png")

    async with client_for(graph, clock) as client:
        await client.send(OutboundImage(to="+573000000001", image=image, caption="Bolso #A7"))

    upload, send = graph.requests
    assert str(upload.url) == MEDIA_URL
    assert upload.headers["Content-Type"].startswith("multipart/form-data")
    assert b'name="messaging_product"' in upload.content
    assert b'filename="upload.png"' in upload.content
    assert b"\x89PNG fixture bytes" in upload.content
    assert json.loads(send.content)["image"] == {"id": MEDIA_ID, "caption": "Bolso #A7"}


async def test_fr_028_media_upload_is_repeated_after_a_read_timeout(clock: Clock) -> None:
    graph = ScriptedGraph(httpx2.ReadTimeout, httpx2.Response(200, json={"id": MEDIA_ID}))

    async with client_for(graph, clock) as client:
        media_id = await client.upload_media(Blob(data=b"fixture", mime_type="image/jpeg"))

    assert media_id == MEDIA_ID
    assert len(graph.requests) == 2


async def test_fr_028_outbound_image_without_a_source_is_refused(clock: Clock) -> None:
    async with client_for(ScriptedGraph(), clock) as client:
        with pytest.raises(ValueError, match="image"):
            await client.send(OutboundImage(to="+573000000001"))


async def test_fr_028_send_failures_are_core_provider_errors(clock: Clock) -> None:
    graph = ScriptedGraph(*[graph_error(429, 130429)] * 3, graph_error(400, 131047))

    async with client_for(graph, clock) as client:
        with pytest.raises(ProviderError) as throttled:
            await client.send_text(CUSTOMER, "Hola")
        with pytest.raises(ProviderError) as window_closed:
            await client.send_text(CUSTOMER, "Hola")

    assert isinstance(throttled.value, WhatsAppSendError)
    assert throttled.value.provider == "whatsapp"
    assert is_retryable(throttled.value)
    assert not is_retryable(window_closed.value)
