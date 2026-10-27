from pathlib import Path
from uuid import UUID

import jwt
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.providers.compose import ProviderMode
from vitrina_core.settings import Settings


def test_health_reports_ok_and_version_under_v1() -> None:
    response = TestClient(create_app()).get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_mock_phone_view_is_mounted_only_when_the_mock_is_on(tmp_path: Path) -> None:
    on = Settings(_env_file=None, whatsapp_mock=True, mock_outbox_path=tmp_path / "outbox.jsonl")  # type: ignore[call-arg]
    off = Settings(_env_file=None, whatsapp_mock=False)  # type: ignore[call-arg]

    assert TestClient(create_app(on)).get("/mock/whatsapp").status_code == 200
    assert TestClient(create_app(off)).get("/mock/whatsapp").status_code == 404


def test_fr_084_every_response_carries_a_request_id_and_errors_are_problems() -> None:
    client = TestClient(create_app())

    health = client.get("/v1/health")
    missing = client.get("/v1/nowhere")

    assert health.headers["x-request-id"]
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"
    assert missing.json()["request_id"] == missing.headers["x-request-id"]


async def test_create_app_resolves_supabase_tokens_to_their_seller() -> None:
    secret = "app-test-jwt-secret-with-32-bytes-or-more"
    app = create_app(Settings(_env_file=None, supabase_jwt_secret=secret))  # type: ignore[call-arg]
    seller_id = UUID("00000000-0000-7000-8000-00000000000a")
    claims = {
        "sub": "subject",
        "aud": "authenticated",
        "exp": 4102444800,
        "seller_id": str(seller_id),
    }
    token = jwt.encode(claims, secret, algorithm="HS256")
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})

    assert await app.state.auth.idempotency_scope(request) == seller_id


def test_as_7_3_the_app_starts_in_replay_mode_without_any_key() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]

    assert app.state.providers.mode is ProviderMode.replay


def test_live_mode_refuses_to_start_without_the_meta_api_key() -> None:
    with pytest.raises(LookupError, match="META_API_KEY"):
        create_app(Settings(_env_file=None, replay=False))  # type: ignore[call-arg]
