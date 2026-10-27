from pathlib import Path

import pytest
from pydantic import ValidationError

from vitrina_core.settings import REPO_ROOT, Settings


def make(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_defaults_are_safe_without_any_environment() -> None:
    settings = make()

    assert settings.replay is True
    assert settings.whatsapp_mock is True
    assert settings.meta_api_base_url == "https://api.meta.ai/v1"
    assert settings.cassette_dir == REPO_ROOT / "evals" / "cassettes"
    assert (settings.mask_iou_min, settings.color_delta_max) == (0.85, 0.25)
    assert settings.outbound_spacing_s == 6.0
    assert settings.provider_attempts == 3


def test_environment_variables_use_the_documented_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY", "false")
    monkeypatch.setenv("DB_SCHEMA", "dev_mx0")
    monkeypatch.setenv("MODEL_AGENT", "agent-model-id")
    monkeypatch.setenv("SERVICE_TOKEN", "local-service-token")

    settings = make()

    assert settings.replay is False
    assert settings.db_schema == "dev_mx0"
    assert settings.model_agent == "agent-model-id"
    assert settings.service_token is not None
    assert settings.service_token.get_secret_value() == "local-service-token"
    assert "local-service-token" not in repr(settings)


def test_unsafe_schema_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="db_schema"):
        make(db_schema='dev"; drop schema public; --')


def test_missing_model_id_names_its_variable() -> None:
    with pytest.raises(LookupError, match="MODEL_STRATEGIST is not set"):
        make().model_id("strategist")

    assert make(model_strategist="wonder-id").model_id("strategist") == "wonder-id"


def test_database_url_gets_the_asyncpg_driver() -> None:
    for url in (
        "postgres://u:p@db.example:5432/postgres",
        "postgresql://u:p@db.example:5432/postgres",
    ):
        assert (
            make(database_url=url).async_database_url()
            == "postgresql+asyncpg://u:p@db.example:5432/postgres"
        )

    with pytest.raises(LookupError, match="DATABASE_URL is not set"):
        make().async_database_url()


def test_env_file_is_read_from_the_repository_root(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DB_SCHEMA=dev_mx2\nSPEND_LIMIT_USD=80\n")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert (settings.db_schema, settings.spend_limit_usd) == ("dev_mx2", 80.0)
