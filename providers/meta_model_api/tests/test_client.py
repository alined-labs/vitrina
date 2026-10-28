import pytest

from vitrina_core.settings import Settings
from vitrina_meta_model_api import MetaChatModel, MetaEmbedder, live_providers, meta_client


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_the_client_needs_meta_api_key_and_says_so() -> None:
    with pytest.raises(LookupError, match="META_API_KEY"):
        meta_client(settings())


def test_the_client_uses_the_settings_and_leaves_retries_to_with_backoff() -> None:
    client = meta_client(
        settings(
            meta_api_key="test-key",
            meta_api_base_url="https://meta.test/v1",
            provider_timeout_s=12.5,
        )
    )

    assert str(client.base_url).rstrip("/") == "https://meta.test/v1"
    assert client.api_key == "test-key"
    assert client.timeout == 12.5
    assert client.max_retries == 0


def test_live_providers_can_give_chat_calls_their_own_timeout() -> None:
    # The worker's chat calls are background jobs: a copy grid took 31 s live (MX1, T034).
    config = settings(meta_api_key="test-key", provider_timeout_s=20.0)

    turns = live_providers(config)
    jobs = live_providers(config, chat_timeout_s=90.0)

    assert isinstance(turns.chat, MetaChatModel)
    assert isinstance(jobs.chat, MetaChatModel)
    assert isinstance(jobs.embedder, MetaEmbedder)
    assert (turns.chat.client.timeout, jobs.chat.client.timeout) == (20.0, 90.0)
    assert jobs.embedder.client.timeout == 20.0
