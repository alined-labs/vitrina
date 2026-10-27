import pytest

from vitrina_core.settings import Settings
from vitrina_meta_model_api import meta_client


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
