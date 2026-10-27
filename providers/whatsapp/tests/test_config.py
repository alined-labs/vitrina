"""FR-020, FR-021, NFR-04: WhatsApp settings come from the environment and never print secrets."""

from vitrina_whatsapp.config import DEFAULT_GRAPH_VERSION, WhatsAppConfig

ENV = {
    "WHATSAPP_APP_SECRET": "fixture-app-secret",
    "WHATSAPP_VERIFY_TOKEN": "fixture-verify-token",
    "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
    "WHATSAPP_ACCESS_TOKEN": "fixture-access-token",
}


def test_fr_021_config_reads_the_whatsapp_env_names() -> None:
    config = WhatsAppConfig.from_env(ENV)

    assert config == WhatsAppConfig(
        phone_number_id="100000000000001",
        app_secret="fixture-app-secret",
        verify_token="fixture-verify-token",
        access_token="fixture-access-token",
        graph_version=DEFAULT_GRAPH_VERSION,
    )


def test_fr_021_graph_version_is_configuration() -> None:
    assert WhatsAppConfig.from_env({**ENV, "WHATSAPP_GRAPH_VERSION": "v25.0"}).graph_version == (
        "v25.0"
    )


def test_fr_021_missing_values_leave_the_adapter_unconfigured() -> None:
    config = WhatsAppConfig.from_env({})

    assert (config.can_receive, config.can_send) == (False, False)
    assert WhatsAppConfig.from_env(ENV).can_receive
    assert WhatsAppConfig.from_env(ENV).can_send


def test_nfr_04_config_repr_never_shows_secrets() -> None:
    shown = repr(WhatsAppConfig.from_env(ENV))

    assert "100000000000001" in shown
    for name in ("WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_ACCESS_TOKEN"):
        assert ENV[name] not in shown
