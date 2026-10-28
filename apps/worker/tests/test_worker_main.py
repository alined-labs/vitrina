import pytest

from vitrina_core.settings import Settings
from vitrina_meta_model_api import MetaChatModel
from vitrina_worker.main import job_providers, serve


async def test_the_worker_refuses_to_start_without_a_database() -> None:
    with pytest.raises(SystemExit, match="needs DATABASE_URL and DB_SCHEMA"):
        await serve(Settings(_env_file=None))  # type: ignore[call-arg]


def test_the_worker_gives_chat_calls_the_job_timeout() -> None:
    config = Settings(_env_file=None, meta_api_key="test-key", provider_job_timeout_s=75.0)  # type: ignore[call-arg]

    providers = job_providers(config)

    assert isinstance(providers.chat, MetaChatModel)
    assert providers.chat.client.timeout == 75.0
