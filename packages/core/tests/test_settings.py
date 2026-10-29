from pathlib import Path

import pytest
from pydantic import ValidationError

from vitrina_core.pricing import ModelPrice
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
    assert settings.public_web_url == "http://localhost:3000"
    assert (settings.agent_reasoning_effort, settings.scope_reasoning_effort) == ("low", "minimal")
    # Background jobs wait longer for a chat answer than a customer's turn (T034), long enough
    # for a Wonder brief, which took 89 s live (T070).
    assert (settings.provider_timeout_s, settings.provider_job_timeout_s) == (20.0, 180.0)


def test_environment_variables_use_the_documented_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY", "false")
    monkeypatch.setenv("DB_SCHEMA", "dev_local")
    monkeypatch.setenv("MODEL_AGENT", "agent-model-id")
    monkeypatch.setenv("SERVICE_TOKEN", "local-service-token")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://vitrina.example")

    settings = make()

    assert settings.replay is False
    assert settings.db_schema == "dev_local"
    assert settings.model_agent == "agent-model-id"
    assert settings.public_web_url == "https://vitrina.example"
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


def test_fr_084_the_default_prices_are_meta_list_prices() -> None:
    prices = make().model_prices

    assert prices["muse-spark-1.3"] == ModelPrice(
        input_per_mtok=1.25, cached_input_per_mtok=0.15, output_per_mtok=4.25
    )
    assert prices["muse-wonder-1.0"] == ModelPrice(
        input_per_mtok=4.0, cached_input_per_mtok=0.2, output_per_mtok=20.0
    )
    assert prices["muse-image-1.0"] == ModelPrice(per_image=0.01)
    assert prices["sam-3.1"] == ModelPrice(per_image=0.0025)
    assert prices["muse-voice-transcribe-1.0"] == ModelPrice(per_audio_hour=0.18)
    # The data-sharing variants are never called (constitution Art. V.2), so they have no price.
    assert not any(model.endswith("-contributor") for model in prices)


def test_fr_084_model_prices_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MODEL_PRICES", '{"agent-model": {"input_per_mtok": 2, "output_per_mtok": 8}}'
    )

    assert make().model_prices == {
        "agent-model": ModelPrice(input_per_mtok=2.0, output_per_mtok=8.0)
    }


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
    env_file.write_text("DB_SCHEMA=dev_other\nSPEND_LIMIT_USD=80\n")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert (settings.db_schema, settings.spend_limit_usd) == ("dev_other", 80.0)


def test_as_8_2_a_blank_visitor_proxy_secret_trusts_no_proxy(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VISITOR_PROXY_SECRET=\n")

    for settings in (
        Settings(_env_file=env_file),  # type: ignore[call-arg]
        make(visitor_proxy_secret="   "),
        make(),
    ):
        assert settings.visitor_proxy_secret is None
        assert not settings.visitor_proxy_matches("")
        assert not settings.visitor_proxy_matches(None)


def test_as_8_2_only_the_proxy_secret_itself_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISITOR_PROXY_SECRET", "proxy-secret-for-tests")

    settings = make()

    assert settings.visitor_proxy_matches("proxy-secret-for-tests")
    assert not settings.visitor_proxy_matches("proxy-secret-for-test")
    assert not settings.visitor_proxy_matches("")
    assert not settings.visitor_proxy_matches(None)
    assert "proxy-secret-for-tests" not in repr(settings)


def test_a_blank_db_schema_is_unset(tmp_path: Path) -> None:
    # A copy of .env.example has `DB_SCHEMA=`: it starts without a database, as if unset.
    env_file = tmp_path / ".env"
    env_file.write_text("DB_SCHEMA=\n")

    from_file = Settings(_env_file=env_file)  # type: ignore[call-arg]
    for settings in (from_file, make(db_schema=""), make(db_schema="  ")):
        assert settings.db_schema is None
    with pytest.raises(ValidationError, match="db_schema"):
        make(db_schema=" dev ")


@pytest.mark.parametrize("name", ["whatsapp_app_secret", "whatsapp_verify_token"])
def test_a_blank_whatsapp_secret_is_unset(name: str) -> None:
    settings = Settings(_env_file=None, **{name: "  "})  # type: ignore[call-arg]
    assert getattr(settings, name) is None
