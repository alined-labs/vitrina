import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from vitrina_core.auth import (
    Authenticator,
    InMemorySellerDirectory,
    SellerId,
    SellerOrService,
    ServiceCaller,
    TokenVerifier,
    WhatsAppSignature,
)
from vitrina_core.http import (
    IdempotencyKey,
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
    RequestIdMiddleware,
    install_problem_handlers,
)

SECRET = "test-jwt-secret-with-at-least-32-bytes!!"
ISSUER = "https://project.supabase.co/auth/v1"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")
SUBJECT = "6f1c2a64-3c2e-4d55-9a1c-5c0d1e2f3a4b"
SERVICE_TOKEN = "service-token-for-tests"


def token(
    key: Any = SECRET, *, alg: str = "HS256", headers: dict[str, str] | None = None, **claims: Any
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": SUBJECT,
        "aud": "authenticated",
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    payload.update(claims)
    return jwt.encode(
        {k: v for k, v in payload.items() if v is not None}, key, algorithm=alg, headers=headers
    )


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def make_app(
    jwks: dict[str, Any] | None = None, service_token: str | None = SERVICE_TOKEN
) -> FastAPI:
    directory = InMemorySellerDirectory({SUBJECT: SELLER})
    verifier = TokenVerifier(secret=SecretStr(SECRET), issuer=ISSUER, jwks=jwks)
    auth = Authenticator(verifier, directory, SecretStr(service_token) if service_token else None)
    app = FastAPI()
    app.state.auth = auth
    install_problem_handlers(app)
    calls: list[str] = []
    app.state.calls = calls

    @app.get("/me")
    def me(seller_id: SellerId) -> dict[str, str]:
        return {"seller_id": str(seller_id)}

    @app.get("/internal")
    def internal(caller: ServiceCaller) -> dict[str, str]:
        return {"kind": caller.kind}

    @app.post("/runs")
    def runs(caller: SellerOrService, _key: IdempotencyKey = None) -> dict[str, str | None]:
        calls.append(caller.kind)
        return {
            "kind": caller.kind,
            "seller_id": str(caller.seller_id) if caller.seller_id else None,
        }

    @app.post("/hook")
    def hook(signature: WhatsAppSignature) -> dict[str, str | None]:
        return {"signature": signature}

    app.add_middleware(
        IdempotencyMiddleware, store=InMemoryIdempotencyStore(), scope=auth.idempotency_scope
    )
    app.add_middleware(RequestIdMiddleware)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(make_app())


def test_seller_token_maps_to_the_seller_by_subject(client: TestClient) -> None:
    response = client.get("/me", headers=bearer(token()))

    assert response.json() == {"seller_id": str(SELLER)}


def test_a_seller_id_claim_from_the_supabase_token_hook_wins(client: TestClient) -> None:
    response = client.get("/me", headers=bearer(token(seller_id=str(OTHER))))

    assert response.json() == {"seller_id": str(OTHER)}


def test_missing_bearer_is_401_unauthorized_with_www_authenticate(client: TestClient) -> None:
    response = client.get("/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "bad",
    [
        token(exp=datetime.now(UTC) - timedelta(minutes=5)),
        token("another-secret-that-is-also-32-bytes-long"),
        token(aud="anon"),
        token(iss="https://evil.example/auth/v1"),
        token(sub=None),
        "not.a.jwt",
        token(alg="none", key=None),
    ],
    ids=["expired", "forged", "audience", "issuer", "no-subject", "garbage", "alg-none"],
)
def test_invalid_tokens_are_401(client: TestClient, bad: str) -> None:
    response = client.get("/me", headers=bearer(bad))

    assert (response.status_code, response.json()["code"]) == (401, "UNAUTHORIZED")


def test_a_valid_token_without_a_seller_is_403(client: TestClient) -> None:
    response = client.get("/me", headers=bearer(token(sub="11111111-2222-4333-8444-555555555555")))

    assert (response.status_code, response.json()["code"]) == (403, "FORBIDDEN")


def test_service_token_is_required_for_internal_routes(client: TestClient) -> None:
    ok = client.get("/internal", headers={"X-Service-Token": SERVICE_TOKEN})
    wrong = client.get("/internal", headers={"X-Service-Token": "guess"})
    seller = client.get("/internal", headers=bearer(token()))

    assert (ok.status_code, ok.json()) == (200, {"kind": "service"})
    assert (wrong.status_code, seller.status_code) == (401, 401)


def test_service_routes_refuse_everyone_when_no_service_token_is_configured() -> None:
    client = TestClient(make_app(service_token=None))

    assert client.get("/internal", headers={"X-Service-Token": SERVICE_TOKEN}).status_code == 401


def test_seller_or_service_routes_accept_either_credential(client: TestClient) -> None:
    as_service = client.post("/runs", headers={"X-Service-Token": SERVICE_TOKEN})
    as_seller = client.post("/runs", headers=bearer(token()))
    anonymous = client.post("/runs")

    assert as_service.json() == {"kind": "service", "seller_id": None}
    assert as_seller.json() == {"kind": "seller", "seller_id": str(SELLER)}
    assert anonymous.status_code == 401


def test_asymmetric_supabase_tokens_verify_against_the_project_jwks() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    jwks = {"keys": [{**public_jwk, "kid": "key-1", "alg": "ES256", "use": "sig"}]}
    client = TestClient(make_app(jwks=jwks))

    good = client.get(
        "/me", headers=bearer(token(private_key, alg="ES256", headers={"kid": "key-1"}))
    )
    unknown_kid = client.get(
        "/me", headers=bearer(token(private_key, alg="ES256", headers={"kid": "key-2"}))
    )

    assert good.json() == {"seller_id": str(SELLER)}
    assert unknown_kid.status_code == 401


def test_whatsapp_signature_header_reaches_the_route(client: TestClient) -> None:
    response = client.post("/hook", headers={"X-Hub-Signature-256": "sha256=abc"})

    assert response.json() == {"signature": "sha256=abc"}


def test_openapi_uses_the_contract_security_schemes() -> None:
    document = make_app().openapi()
    schemes = document["components"]["securitySchemes"]

    assert schemes["sellerAuth"] | {"description": None} == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": None,
    }
    assert {key: schemes["serviceAuth"][key] for key in ("type", "in", "name")} == {
        "type": "apiKey",
        "in": "header",
        "name": "X-Service-Token",
    }
    assert schemes["whatsappSignature"]["name"] == "X-Hub-Signature-256"
    assert document["paths"]["/me"]["get"]["security"] == [{"sellerAuth": []}]
    assert sorted(map(str, document["paths"]["/runs"]["post"]["security"])) == [
        "{'sellerAuth': []}",
        "{'serviceAuth': []}",
    ]


def test_fr_081_idempotency_keys_belong_to_the_seller_in_the_token() -> None:
    app = make_app()
    client = TestClient(app)
    directory = app.state.auth.directory
    directory.subjects["7a7a7a7a-0000-4000-8000-000000000002"] = OTHER
    other_token = token(sub="7a7a7a7a-0000-4000-8000-000000000002")
    headers = {"Idempotency-Key": "same-key-for-two-sellers"}

    client.post("/runs", headers={**headers, **bearer(token())})
    client.post("/runs", headers={**headers, **bearer(other_token)})
    client.post("/runs", headers={**headers, **bearer(token())})

    assert app.state.calls == ["seller", "seller"]
