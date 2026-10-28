from pathlib import Path

import pytest

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.pricing import ModelPrice
from vitrina_core.providers import (
    Blob,
    CallMeta,
    CassetteMissError,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImageRequest,
    ProviderError,
)
from vitrina_core.providers.compose import LiveProviders, ProviderMode, compose
from vitrina_core.settings import Settings

REQUEST = ChatRequest(
    model="model-from-settings",
    purpose="agent",
    messages=(ChatMessage(role="user", content="¿Tienen la mochila azul?"),),
)


class FakeChat:
    def __init__(self, cost: float | None = 0.001) -> None:
        self.calls = 0
        self.cost = cost

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        return ChatResponse(
            message=ChatMessage(role="assistant", content="Sí, la tenemos."),
            meta=CallMeta(
                provider="meta_model_api",
                model=request.model,
                purpose=request.purpose,
                tokens_in=30,
                tokens_out=5,
                cost_usd=self.cost,
            ),
        )


def settings(tmp_path: Path, **values: object) -> Settings:
    return Settings(_env_file=None, cassette_dir=tmp_path, **values)  # type: ignore[arg-type]


def no_live() -> LiveProviders:
    raise AssertionError("replay mode must not build live providers (no keys, AS-7.3)")


def test_the_mode_follows_replay_and_record_cassettes(tmp_path: Path) -> None:
    assert ProviderMode.from_settings(settings(tmp_path)) is ProviderMode.replay
    assert (
        ProviderMode.from_settings(settings(tmp_path, record_cassettes=True)) is ProviderMode.replay
    )
    assert ProviderMode.from_settings(settings(tmp_path, replay=False)) is ProviderMode.live
    assert (
        ProviderMode.from_settings(settings(tmp_path, replay=False, record_cassettes=True))
        is ProviderMode.record
    )


async def test_as_7_3_replay_answers_from_cassettes_without_live_providers(
    tmp_path: Path,
) -> None:
    live = FakeChat()
    recording = compose(
        settings(tmp_path, replay=False, record_cassettes=True),
        InMemoryLedger(),
        lambda: LiveProviders(chat=live),
    )
    await recording.chat.complete(REQUEST)
    ledger = InMemoryLedger()

    providers = compose(settings(tmp_path), ledger, no_live)
    response = await providers.chat.complete(REQUEST)

    assert providers.mode is ProviderMode.replay
    assert response.message.content == "Sí, la tenemos."
    assert live.calls == 1
    # Every call reaches the ledger, replayed ones too, and costs nothing there (ADR-0010).
    [entry] = ledger.entries
    assert entry.replayed is True


async def test_as_7_3_replay_without_a_cassette_misses_instead_of_calling_out(
    tmp_path: Path,
) -> None:
    providers = compose(settings(tmp_path), InMemoryLedger(), no_live)

    with pytest.raises(CassetteMissError):
        await providers.chat.complete(REQUEST)


async def test_fr_084_record_mode_calls_live_writes_a_cassette_and_ledgers_the_cost(
    tmp_path: Path,
) -> None:
    live, ledger = FakeChat(), InMemoryLedger()
    providers = compose(
        settings(tmp_path, replay=False, record_cassettes=True),
        ledger,
        lambda: LiveProviders(chat=live),
    )

    await providers.chat.complete(REQUEST)

    assert live.calls == 1
    assert len(list((tmp_path / "chat").glob("*.json"))) == 1
    [entry] = ledger.entries
    assert (entry.replayed, entry.cost_usd, entry.tokens_in) == (False, 0.001, 30)


async def test_fr_084_live_calls_are_priced_from_the_settings(tmp_path: Path) -> None:
    ledger = InMemoryLedger()
    prices = {"model-from-settings": ModelPrice(input_per_mtok=1000.0, output_per_mtok=2000.0)}
    providers = compose(
        settings(tmp_path, replay=False, model_prices=prices),
        ledger,
        lambda: LiveProviders(chat=FakeChat(cost=None)),
    )

    response = await providers.chat.complete(REQUEST)

    # 30 tokens in at $1,000 and 5 out at $2,000 per million.
    assert response.meta.cost_usd == pytest.approx(0.04)
    assert [entry.cost_usd for entry in ledger.entries] == pytest.approx([0.04])


async def test_live_mode_calls_the_adapter_directly(tmp_path: Path) -> None:
    live, ledger = FakeChat(), InMemoryLedger()
    providers = compose(settings(tmp_path, replay=False), ledger, lambda: LiveProviders(chat=live))

    await providers.chat.complete(REQUEST)

    assert live.calls == 1
    assert not (tmp_path / "chat").exists()
    assert [entry.replayed for entry in ledger.entries] == [False]


async def test_live_mode_without_an_adapter_fails_on_the_call_not_at_startup(
    tmp_path: Path,
) -> None:
    providers = compose(
        settings(tmp_path, replay=False), InMemoryLedger(), lambda: LiveProviders(chat=FakeChat())
    )
    request = ImageRequest(
        model="image-model-from-settings",
        purpose="scene",
        prompt="Mochila azul sobre una mesa de madera",
        references=(Blob(data=b"png", mime_type="image/png"),),
    )

    with pytest.raises(ProviderError, match="no live image adapter") as caught:
        await providers.image.generate(request)

    assert not caught.value.retryable


async def test_record_mode_without_an_adapter_keeps_replaying_that_role(
    tmp_path: Path,
) -> None:
    providers = compose(
        settings(tmp_path, replay=False, record_cassettes=True),
        InMemoryLedger(),
        lambda: LiveProviders(chat=FakeChat()),
    )
    request = ImageRequest(
        model="image-model-from-settings", purpose="scene", prompt="Mochila azul"
    )

    with pytest.raises(CassetteMissError):
        await providers.image.generate(request)


async def test_an_explicit_mode_overrides_the_settings(tmp_path: Path) -> None:
    # POST /evals/runs with replay=true on a live process (ADR-0010).
    providers = compose(
        settings(tmp_path, replay=False), InMemoryLedger(), no_live, mode=ProviderMode.replay
    )

    assert providers.mode is ProviderMode.replay
    with pytest.raises(CassetteMissError):
        await providers.chat.complete(REQUEST)
