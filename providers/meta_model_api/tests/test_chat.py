import base64
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx2
import pytest

from vitrina_core.providers import (
    Blob,
    CassetteMode,
    Cassettes,
    ChatMessage,
    ChatRequest,
    ImagePart,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
    RecordedChatModel,
    ResponseFormat,
    TextPart,
    ToolCall,
    ToolSpec,
    with_backoff,
)
from vitrina_meta_model_api import MetaChatModel
from vitrina_meta_model_api.client import retry_after_s
from vitrina_meta_model_api.testing import FakeApi, completion, error

SEARCH = ToolSpec(
    name="search_catalog",
    description="Buscar productos y datos del negocio.",
    parameters={
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {"query": {"type": "string", "maxLength": 300}},
    },
)
REQUEST = ChatRequest(
    model="model-from-settings",
    purpose="agent",
    messages=(
        ChatMessage(role="system", content="Eres el asistente de ventas de Tienda Ejemplo."),
        ChatMessage(role="user", content="¿Tienen la mochila azul?"),
    ),
)
TOOL_CALL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search_catalog", "arguments": '{"query": "mochila azul"}'},
        }
    ],
}


async def test_chat_sends_tools_and_the_json_schema_in_openai_form(api: FakeApi) -> None:
    api.answer(completion({"role": "assistant", "content": "{}"}))
    request = REQUEST.model_copy(
        update={
            "tools": (SEARCH,),
            "tool_choice": "auto",
            "response_format": ResponseFormat(name="agent_turn", json_schema={"type": "object"}),
            "temperature": 0.2,
            "max_tokens": 400,
        }
    )

    await MetaChatModel(api.client()).complete(request)

    sent = api.requests[0]
    assert sent.url == "https://meta.test/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer test-key"
    assert api.body() == {
        "model": "model-from-settings",
        "messages": [
            {"role": "system", "content": "Eres el asistente de ventas de Tienda Ejemplo."},
            {"role": "user", "content": "¿Tienen la mochila azul?"},
        ],
        # agent-tools.json parameters travel verbatim (ADR-0003).
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search_catalog",
                    "description": "Buscar productos y datos del negocio.",
                    "parameters": SEARCH.parameters,
                },
            }
        ],
        "tool_choice": "auto",
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "agent_turn", "schema": {"type": "object"}, "strict": True},
        },
        "temperature": 0.2,
        "max_completion_tokens": 400,
    }


async def test_chat_answers_tool_choice_none_by_sending_no_tools(api: FakeApi) -> None:
    # The API takes tool_choice "auto" only and answers 400 otherwise; a request without tools
    # cannot call one, and the tool calls already in the history are accepted (T011 spike).
    api.answer(completion({"role": "assistant", "content": "{}"}))
    request = REQUEST.model_copy(update={"tools": (SEARCH,), "tool_choice": "none"})

    await MetaChatModel(api.client()).complete(request)

    assert "tools" not in api.body()
    assert "tool_choice" not in api.body()


async def test_chat_sends_the_reasoning_effort_only_when_set(api: FakeApi) -> None:
    api.answer(
        completion({"role": "assistant", "content": "hola"}),
        completion({"role": "assistant", "content": "hola"}),
    )
    chat = MetaChatModel(api.client())

    await chat.complete(REQUEST.model_copy(update={"reasoning_effort": "low"}))
    await chat.complete(REQUEST)

    assert api.body(0)["reasoning_effort"] == "low"
    assert "reasoning_effort" not in api.body(1)


async def test_chat_sends_images_as_base64_data_urls(api: FakeApi) -> None:
    api.answer(completion({"role": "assistant", "content": "Una mochila azul."}))
    photo = Blob(data=b"\x89PNG fake bytes", mime_type="image/png")
    request = REQUEST.model_copy(
        update={
            "purpose": "verify",
            "messages": (
                ChatMessage(
                    role="user",
                    content=(TextPart(text="¿Qué producto es?"), ImagePart(image=photo)),
                ),
            ),
        }
    )

    await MetaChatModel(api.client()).complete(request)

    encoded = base64.b64encode(photo.data).decode()
    assert api.body()["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "¿Qué producto es?"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        }
    ]


async def test_chat_sends_the_tool_round_back_with_its_call_ids(api: FakeApi) -> None:
    api.answer(completion({"role": "assistant", "content": "Sí, la tenemos."}))
    call = ToolCall(id="call_1", name="search_catalog", arguments='{"query": "mochila azul"}')
    request = REQUEST.model_copy(
        update={
            "messages": (
                *REQUEST.messages,
                ChatMessage(role="assistant", tool_calls=(call,)),
                ChatMessage(role="tool", tool_call_id="call_1", content='{"results": []}'),
            )
        }
    )

    await MetaChatModel(api.client()).complete(request)

    assert api.body()["messages"][2:] == [
        {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL["tool_calls"][0]]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"results": []}'},
    ]


async def test_fr_084_the_answer_carries_tool_calls_tokens_and_latency(api: FakeApi) -> None:
    api.answer(completion(TOOL_CALL, finish_reason="tool_calls"))

    response = await MetaChatModel(api.client()).complete(REQUEST)

    assert response.message == ChatMessage(
        role="assistant",
        content=None,
        tool_calls=(
            ToolCall(id="call_1", name="search_catalog", arguments='{"query": "mochila azul"}'),
        ),
    )
    assert response.finish_reason == "tool_calls"
    meta = response.meta
    assert (meta.provider, meta.model, meta.purpose) == (
        "meta_model_api",
        "model-from-settings",
        "agent",
    )
    assert (meta.tokens_in, meta.tokens_out, meta.tokens_cached) == (120, 18, 0)
    assert meta.latency_ms >= 0
    # The API reports no price; the ledger prices the call from its list price.
    assert meta.cost_usd is None
    assert meta.replayed is False


@pytest.mark.parametrize("usage", [None, {}], ids=["no usage", "empty usage"])
async def test_an_answer_without_token_counts_counts_zero(
    api: FakeApi, usage: dict[str, int] | None
) -> None:
    api.answer(completion({"role": "assistant", "content": "Hola"}, usage=usage))

    response = await MetaChatModel(api.client()).complete(REQUEST)

    assert response.message.content == "Hola"
    assert (response.meta.tokens_in, response.meta.tokens_out, response.meta.tokens_cached) == (
        0,
        0,
        0,
    )


async def test_fr_084_prompt_tokens_served_from_cache_are_counted_apart(api: FakeApi) -> None:
    usage = {
        "prompt_tokens": 4488,
        "completion_tokens": 58,
        "total_tokens": 4546,
        "prompt_tokens_details": {"cached_tokens": 4465},
    }
    api.answer(completion({"role": "assistant", "content": "Hola"}, usage=usage))

    response = await MetaChatModel(api.client()).complete(REQUEST)

    assert (response.meta.tokens_in, response.meta.tokens_cached) == (4488, 4465)


async def test_the_same_adapter_serves_another_compatible_endpoint(api: FakeApi) -> None:
    api.answer(completion({"role": "assistant", "content": "Hola"}))

    response = await MetaChatModel(api.client(), provider="baseten").complete(REQUEST)

    assert response.meta.provider == "baseten"


async def test_as_3_11_a_429_is_rate_limited_with_its_retry_after_and_one_request(
    api: FakeApi,
) -> None:
    api.answer(error(429, "Too many requests", **{"retry-after": "7"}))

    with pytest.raises(RateLimitedError) as caught:
        await MetaChatModel(api.client()).complete(REQUEST)

    assert caught.value.retry_after_s == 7.0
    assert caught.value.retryable
    # SDK retries are off: the backoff policy (R12) is with_backoff's alone.
    assert len(api.requests) == 1


async def test_as_3_11_with_backoff_waits_the_retry_after_then_succeeds(api: FakeApi) -> None:
    api.answer(
        error(429, "Too many requests", **{"retry-after": "3"}),
        completion({"role": "assistant", "content": "Hola"}),
    )
    chat = MetaChatModel(api.client())
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    response = await with_backoff(lambda: chat.complete(REQUEST), sleep=sleep)

    assert response.message.content == "Hola"
    assert waits == [3.0]
    assert len(api.requests) == 2


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (error(500, "internal"), 500),
        (error(503, "overloaded"), 503),
        (error(408, "request timeout"), 408),
        (httpx2.ReadTimeout("read timed out"), None),
        (httpx2.ConnectError("connection refused"), None),
    ],
    ids=["500", "503", "408", "timeout", "connection"],
)
async def test_art_viii_1_outages_and_timeouts_are_unavailable_and_retryable(
    api: FakeApi, answer: httpx2.Response | Exception, status: int | None
) -> None:
    api.answer(answer)

    with pytest.raises(ProviderUnavailableError) as caught:
        await MetaChatModel(api.client()).complete(REQUEST)

    assert caught.value.retryable
    assert caught.value.status_code == status
    assert caught.value.provider == "meta_model_api"


@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_request_errors_are_final_and_keep_the_providers_reason(
    api: FakeApi, status: int
) -> None:
    # ADR-0005: a rejected response_format must be visible to the caller's fallback.
    api.answer(error(status, "response_format json_schema is not supported"))

    with pytest.raises(ProviderError) as caught:
        await MetaChatModel(api.client()).complete(REQUEST)

    assert not isinstance(caught.value, ProviderUnavailableError)
    assert not caught.value.retryable
    assert caught.value.status_code == status
    assert "response_format json_schema is not supported" in str(caught.value)
    assert len(api.requests) == 1


async def test_an_answer_without_choices_is_unavailable(api: FakeApi) -> None:
    api.answer(
        httpx2.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [],
            },
        )
    )

    with pytest.raises(ProviderUnavailableError):
        await MetaChatModel(api.client()).complete(REQUEST)


def test_retry_after_reads_seconds_milliseconds_and_dates() -> None:
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    later = format_datetime(now + timedelta(seconds=30), usegmt=True)

    assert retry_after_s(httpx2.Headers({"retry-after": "7"}), now=now) == 7.0
    assert retry_after_s(httpx2.Headers({"retry-after": "1.5"}), now=now) == 1.5
    assert retry_after_s(httpx2.Headers({"retry-after-ms": "250"}), now=now) == 0.25
    assert retry_after_s(httpx2.Headers({"retry-after": later}), now=now) == 30.0
    assert retry_after_s(httpx2.Headers({"retry-after": "soon"}), now=now) is None
    assert retry_after_s(httpx2.Headers({"retry-after": "-3"}), now=now) is None
    assert retry_after_s(httpx2.Headers(), now=now) is None


async def test_as_7_3_a_recorded_meta_call_replays_without_key_or_network(
    api: FakeApi, tmp_path: Path
) -> None:
    api.answer(completion(TOOL_CALL, finish_reason="tool_calls"))
    live = MetaChatModel(api.client())
    recorded = await RecordedChatModel(Cassettes(tmp_path, CassetteMode.record), live).complete(
        REQUEST
    )

    replayed = await RecordedChatModel(Cassettes(tmp_path, CassetteMode.replay)).complete(REQUEST)

    assert replayed.message == recorded.message
    assert replayed.meta.replayed is True
    assert len(api.requests) == 1
