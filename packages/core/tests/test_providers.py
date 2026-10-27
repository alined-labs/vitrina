import json
from pathlib import Path

import pytest

from vitrina_core.providers import (
    Blob,
    CallMeta,
    CassetteMissError,
    CassetteMode,
    Cassettes,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderError,
    RateLimitedError,
    RecordedChatModel,
    RecordedEmbedder,
    ToolCall,
    ToolSpec,
    with_backoff,
)

REQUEST = ChatRequest(
    model="model-from-settings",
    purpose="agent",
    messages=(
        ChatMessage(role="system", content="Eres el asistente de ventas."),
        ChatMessage(role="user", content="¿Cuánto vale el envío a Medellín?"),
    ),
    tools=(ToolSpec(name="search_catalog", description="Buscar", parameters={"type": "object"}),),
)


def reply(text: str) -> ChatResponse:
    return ChatResponse(
        message=ChatMessage(
            role="assistant",
            content=text,
            tool_calls=(
                ToolCall(id="call_1", name="search_catalog", arguments='{"query": "envío"}'),
            ),
        ),
        finish_reason="tool_calls",
        meta=CallMeta(
            provider="meta_model_api",
            model="model-from-settings",
            purpose="agent",
            tokens_in=42,
            tokens_out=7,
            latency_ms=812,
        ),
    )


class FakeChat:
    def __init__(self) -> None:
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return reply("Déjame revisar.")


async def test_recorded_chat_model_replays_what_it_recorded(tmp_path: Path) -> None:
    live = FakeChat()
    recorder: ChatModel = RecordedChatModel(Cassettes(tmp_path, CassetteMode.record), live)
    recorded = await recorder.complete(REQUEST)

    player: ChatModel = RecordedChatModel(Cassettes(tmp_path, CassetteMode.replay))
    replayed = await player.complete(REQUEST)

    assert len(live.calls) == 1
    assert recorded.meta.replayed is False
    assert replayed.meta.replayed is True
    assert replayed.message == recorded.message
    assert replayed.meta.tokens_in == 42


async def test_replay_without_a_cassette_raises_cassette_miss(tmp_path: Path) -> None:
    player = RecordedChatModel(Cassettes(tmp_path, CassetteMode.replay))

    with pytest.raises(CassetteMissError, match="no cassette for chat"):
        await player.complete(REQUEST)


async def test_record_mode_needs_a_live_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs a live provider"):
        RecordedChatModel(Cassettes(tmp_path, CassetteMode.record))


def test_cassette_key_is_stable_and_follows_the_request(tmp_path: Path) -> None:
    cassettes = Cassettes(tmp_path, CassetteMode.replay)
    changed = REQUEST.model_copy(update={"temperature": 0.2})

    assert cassettes.key("chat", REQUEST) == cassettes.key("chat", REQUEST.model_copy())
    assert cassettes.key("chat", REQUEST) != cassettes.key("chat", changed)
    assert cassettes.key("chat", REQUEST) != cassettes.key("embed", REQUEST)


async def test_cassette_file_summarizes_binary_inputs(tmp_path: Path) -> None:
    image = Blob(data=b"\x89PNG fake image bytes", mime_type="image/png")
    request = EmbeddingRequest(model="embedder-from-settings", images=(image,))

    class FakeEmbedder:
        async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
            return EmbeddingResponse(
                vectors=((0.1, 0.2),),
                meta=CallMeta(provider="meta_model_api", model=request.model, purpose="embed"),
            )

    await RecordedEmbedder(Cassettes(tmp_path, CassetteMode.record), FakeEmbedder()).embed(request)

    [cassette] = (tmp_path / "embed").glob("*.json")
    stored = json.loads(cassette.read_text())
    assert stored["request"]["images"][0] == {
        "mime_type": "image/png",
        "sha256": image.sha256,
        "size": len(image.data),
    }
    replayed = await RecordedEmbedder(Cassettes(tmp_path, CassetteMode.replay)).embed(request)
    assert replayed.vectors == ((0.1, 0.2),)


def test_embedding_request_needs_exactly_one_kind_of_input() -> None:
    with pytest.raises(ValueError, match="exactly one of texts or images"):
        EmbeddingRequest(model="embedder-from-settings")


async def test_as_3_11_backoff_retries_429_at_most_three_attempts() -> None:
    attempts: list[int] = []
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    async def always_rate_limited() -> str:
        attempts.append(1)
        raise RateLimitedError("429 from upstream", provider="meta_model_api")

    with pytest.raises(RateLimitedError):
        await with_backoff(always_rate_limited, sleep=sleep)

    assert len(attempts) == 3
    assert delays == [0.5, 1.0]


async def test_backoff_returns_after_a_transient_failure_and_skips_permanent_ones() -> None:
    outcomes: list[Exception | str] = [
        RateLimitedError("429", provider="whatsapp", retry_after_s=2.0),
        "ok",
    ]

    async def flaky() -> str:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def no_sleep(seconds: float) -> None:
        assert seconds == 2.0  # Retry-After wins over the computed delay

    assert await with_backoff(flaky, sleep=no_sleep) == "ok"

    async def permanent() -> str:
        raise ProviderError("400 bad request", provider="whatsapp")

    with pytest.raises(ProviderError, match="400"):
        await with_backoff(permanent, sleep=no_sleep)
