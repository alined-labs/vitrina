"""T083 routes without a database: 503, and a malformed slug is refused before anything else."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from vitrina_api.main import create_app
from vitrina_api.routers.public import visitor_address
from vitrina_core.settings import Settings
from vitrina_creative.testing import source_photo


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, storage_dir=tmp_path)))  # type: ignore[call-arg]


def test_showcases_need_the_database_and_no_sign_in(client: TestClient) -> None:
    created = client.post(
        "/v1/public/showcases",
        files={"image": ("bolso.png", source_photo().image.data, "image/png")},
        data={"product_name": "Bolso tejido a mano"},
    )
    fetched = client.get("/v1/public/showcases/bolso-tejido-a-mano-3f9a1c")

    assert (created.status_code, created.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")
    assert (fetched.status_code, fetched.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")


def test_a_malformed_slug_is_a_validation_error(client: TestClient) -> None:
    response = client.get("/v1/public/showcases/NOT_A_SLUG")

    assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_ERROR")


def visitor_request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("10.0.0.1", 41000),
    }
    return Request(scope)


def test_as_8_2_behind_the_proxy_each_visitor_is_limited_by_their_own_address() -> None:
    assert visitor_address(visitor_request({"X-Vitrina-Visitor": "203.0.113.7"})) == "203.0.113.7"
    assert visitor_address(visitor_request({"X-Vitrina-Visitor": " 2001:db8::1 "})) == "2001:db8::1"


def test_without_a_valid_visitor_header_the_client_address_counts() -> None:
    assert visitor_address(visitor_request({})) == "10.0.0.1"
    assert (
        visitor_address(visitor_request({"X-Vitrina-Visitor": "anything, 1.2.3.4"})) == "10.0.0.1"
    )
