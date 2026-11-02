"""Who may read a stored response, and whose body the API holds before it knows the caller."""

from datetime import timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.http import idempotency
from vitrina_core.settings import Settings

SECRET = "idempotency-callers-test-secret-32-bytes+"
SERVICE_TOKEN = "service-token-for-tests"
OPERATOR = {"X-Service-Token": SERVICE_TOKEN}
KEY = {"Idempotency-Key": "create-seller-0001"}
NEW_SELLER = {
    "business_name": "Bolsos Ana",
    "owner_name": "Ana",
    "whatsapp_number": "+573000000441",
    "city": "Medellín",
}


def bearer(subject: str, **claims: Any) -> dict[str, str]:
    payload = {"sub": subject, "aud": "authenticated", "exp": 4102444800, **claims}
    return {"Authorization": f"Bearer {jwt.encode(payload, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=SECRET, service_token=SERVICE_TOKEN)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


def test_fr_081_a_new_seller_is_replayed_to_the_operator_and_to_nobody_else(
    client: TestClient,
) -> None:
    created = client.post("/v1/sellers", json=NEW_SELLER, headers={**OPERATOR, **KEY})
    assert created.status_code == 201

    strangers = [
        client.post("/v1/sellers", json=NEW_SELLER, headers=KEY),
        client.post("/v1/sellers", json=NEW_SELLER, headers={**KEY, **bearer("someone-else")}),
        client.post(
            "/v1/sellers", json=NEW_SELLER, headers={**KEY, "X-Service-Token": "not-the-token"}
        ),
    ]
    for stranger in strangers:
        assert stranger.status_code != 201
        assert "idempotent-replayed" not in stranger.headers
        assert created.json()["id"] not in stranger.text

    replayed = client.post("/v1/sellers", json=NEW_SELLER, headers={**OPERATOR, **KEY})
    assert replayed.headers["idempotent-replayed"] == "true"
    assert replayed.json() == created.json()


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"Authorization": "Bearer not-a-token"},
        {"X-Service-Token": "not-the-token"},
        # Signed in, but no seller: anyone can get such an account, and this route needs a seller.
        bearer("account-without-a-seller"),
    ],
)
def test_fr_081_an_upload_without_a_caller_is_refused_with_its_body_unbuffered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, credentials: dict[str, str]
) -> None:
    async def never(_receive: Any) -> bytes:
        raise AssertionError("the body was buffered before the caller was known")

    monkeypatch.setattr(idempotency, "_read_body", never)

    response = client.post(
        "/v1/assets",
        files={"file": ("a.jpg", b"x" * 2048, "image/jpeg")},
        headers={"Idempotency-Key": "upload-key-0001", **credentials},
    )

    assert response.status_code in {401, 403}


@pytest.mark.parametrize("path", ["/v1/public/showcases", "/v1/webhooks/whatsapp"])
def test_fr_081_an_open_route_is_not_buffered_for_an_anonymous_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async def never(_receive: Any) -> bytes:
        raise AssertionError("the body was buffered before the caller was known")

    monkeypatch.setattr(idempotency, "_read_body", never)

    response = client.post(path, content=b"{}", headers={"Idempotency-Key": "open-route-key-01"})

    assert response.status_code < 500 or response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_fr_081_an_upload_over_its_limit_is_still_refused_for_an_anonymous_caller(
    client: TestClient,
) -> None:
    def stream() -> Any:
        for _ in range(70):
            yield b"x" * (1024 * 1024)

    response = client.post(
        "/v1/assets",
        content=stream(),
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "Idempotency-Key": "too-large-key-01",
        },
    )

    assert (response.status_code, response.json()["code"]) == (413, "PAYLOAD_TOO_LARGE")
    assert response.headers["x-request-id"]


def test_fr_081_the_stale_limit_comes_from_the_settings() -> None:
    settings = Settings(_env_file=None, idempotency_stale_after_s=12)  # type: ignore[call-arg]

    app = create_app(settings)

    middleware = next(m for m in app.user_middleware if m.cls is idempotency.IdempotencyMiddleware)
    assert middleware.kwargs["store"].stale_after == timedelta(seconds=12)
    assert middleware.kwargs["renew_every_s"] == 2
