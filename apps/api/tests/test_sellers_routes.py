import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings

SECRET = "sellers-routes-test-secret-with-32-bytes+"
SUBJECT = "6f1c2a64-3c2e-4d55-9a1c-5c0d1e2f3a4b"
SERVICE_TOKEN = "service-token-for-tests"
OPERATOR = {"X-Service-Token": SERVICE_TOKEN}
NEW_SELLER = {
    "business_name": "Bolsos Ana",
    "owner_name": "Ana",
    "whatsapp_number": "+573000000001",
    "city": "Medellín",
}


def bearer(subject: str = SUBJECT) -> dict[str, str]:
    claims = {"sub": subject, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=SECRET, service_token=SERVICE_TOKEN)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


def as_operator(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {**OPERATOR, **(headers or {})}


def test_as_1_1_a_new_seller_gets_the_default_settings(client: TestClient) -> None:
    response = client.post("/v1/sellers", json=NEW_SELLER, headers=OPERATOR)

    assert response.status_code == 201
    seller = response.json()
    assert {key: seller[key] for key in NEW_SELLER} == NEW_SELLER
    assert seller["timezone"] == "America/Bogota"
    assert seller["category"] == "products"
    assert (seller["handoff_amount_threshold_cop"], seller["brand_voice_min"]) == (300000, 3.5)
    assert (seller["ai_label_enabled"], seller["handoff_summary_enabled"]) == (True, True)
    assert (
        seller["consent_prompt_text"]
        == "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"
    )
    assert seller["marketing_template_status"] == "none"


def test_as_1_1_an_anonymous_caller_cannot_claim_a_whatsapp_number(client: TestClient) -> None:
    response = client.post("/v1/sellers", json=NEW_SELLER)

    assert (response.status_code, response.json()["code"]) == (401, "UNAUTHORIZED")
    assert client.get("/v1/sellers/me", headers=bearer()).status_code == 403  # nothing was created


def test_as_1_1_a_signed_in_user_alone_cannot_claim_a_whatsapp_number(client: TestClient) -> None:
    response = client.post("/v1/sellers", json=NEW_SELLER, headers=bearer())

    assert response.status_code == 401
    assert client.get("/v1/sellers/me", headers=bearer()).status_code == 403


def test_as_1_1_a_wrong_operator_token_is_refused(client: TestClient) -> None:
    wrong = {"X-Service-Token": "not-the-token"}

    assert client.post("/v1/sellers", json=NEW_SELLER, headers=wrong).status_code == 401


def test_as_1_1_without_a_configured_operator_token_nobody_creates_a_seller() -> None:
    unset = TestClient(create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET)))  # type: ignore[call-arg]

    for token in (SERVICE_TOKEN, ""):
        response = unset.post("/v1/sellers", json=NEW_SELLER, headers={"X-Service-Token": token})
        assert response.status_code == 401


def test_signing_up_with_a_token_links_the_account_to_the_seller(client: TestClient) -> None:
    created = client.post("/v1/sellers", json=NEW_SELLER, headers=as_operator(bearer())).json()

    me = client.get("/v1/sellers/me", headers=bearer())

    assert me.status_code == 200
    assert me.json()["id"] == created["id"]


def test_a_number_or_account_with_a_seller_cannot_sign_up_again(client: TestClient) -> None:
    client.post("/v1/sellers", json=NEW_SELLER, headers=as_operator(bearer()))

    same_number = client.post("/v1/sellers", json=NEW_SELLER, headers=OPERATOR)
    same_account = client.post(
        "/v1/sellers",
        json={**NEW_SELLER, "whatsapp_number": "+573000000002"},
        headers=as_operator(bearer()),
    )

    assert (same_number.status_code, same_number.json()["code"]) == (409, "CONFLICT")
    assert same_account.status_code == 409


def test_whatsapp_numbers_must_be_e164(client: TestClient) -> None:
    response = client.post(
        "/v1/sellers", json={**NEW_SELLER, "whatsapp_number": "300 000 0001"}, headers=OPERATOR
    )

    assert (response.status_code, response.json()["errors"][0]["field"]) == (400, "whatsapp_number")


def test_sellers_update_their_settings(client: TestClient) -> None:
    client.post("/v1/sellers", json=NEW_SELLER, headers=as_operator(bearer()))

    response = client.patch(
        "/v1/sellers/me",
        json={
            "business_hours": {"mon": [["09:00", "18:00"]]},
            "handoff_amount_threshold_cop": 500000,
        },
        headers=bearer(),
    )

    assert response.status_code == 200
    assert response.json()["business_hours"] == {"mon": [["09:00", "18:00"]]}
    assert response.json()["handoff_amount_threshold_cop"] == 500000
    bad = client.patch(
        "/v1/sellers/me", json={"business_hours": {"mon": [["9am", "6pm"]]}}, headers=bearer()
    )
    assert bad.status_code == 400


def test_a_signed_in_account_without_a_seller_is_forbidden(client: TestClient) -> None:
    assert client.get("/v1/sellers/me", headers=bearer()).status_code == 403
    assert client.get("/v1/sellers/me").status_code == 401


def test_voice_samples_check_the_export_before_needing_the_database(client: TestClient) -> None:
    client.post("/v1/sellers", json=NEW_SELLER, headers=as_operator(bearer()))
    export = "27/10/26, 14:05 - Ana Bolsos: Hola veci!\n27/10/26, 14:06 - Cliente: hola\n"

    unknown = client.post(
        "/v1/sellers/me/voice-samples",
        files={"file": ("chat.txt", export.encode())},
        data={"seller_display_name": "Nadie"},
        headers=bearer(),
    )
    no_database = client.post(
        "/v1/sellers/me/voice-samples",
        files={"file": ("chat.txt", export.encode())},
        data={"seller_display_name": "Ana Bolsos"},
        headers=bearer(),
    )

    assert (unknown.status_code, unknown.json()["errors"][0]["field"]) == (
        400,
        "seller_display_name",
    )
    assert (no_database.status_code, no_database.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")
