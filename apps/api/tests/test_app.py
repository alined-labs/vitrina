from pathlib import Path

from fastapi.testclient import TestClient

from vitrina_api.main import create_app
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
