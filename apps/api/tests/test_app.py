from fastapi.testclient import TestClient

from vitrina_api.main import create_app


def test_health_reports_ok_and_version_under_v1() -> None:
    response = TestClient(create_app()).get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
