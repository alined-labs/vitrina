"""Every upload route has a body limit of its own; any other body over 1 MB is refused unread."""

import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import API_PREFIX, BODY_LIMITS, create_app
from vitrina_core.settings import Settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))  # type: ignore[call-arg]


def test_each_multipart_route_and_the_webhook_has_its_own_limit(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    uploads = {
        path
        for path, operations in schema["paths"].items()
        for operation in operations.values()
        if "multipart/form-data" in operation.get("requestBody", {}).get("content", {})
    }
    assert uploads
    assert uploads | {f"{API_PREFIX}/webhooks/whatsapp"} == set(BODY_LIMITS)


def test_a_json_body_over_1_mb_is_refused_before_anything_reads_it(client: TestClient) -> None:
    response = client.post(
        f"{API_PREFIX}/sellers",
        content=b"{" + b" " * (1024 * 1024) + b"}",
        headers={"content-type": "application/json", "idempotency-key": "k" * 20},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["x-request-id"]
