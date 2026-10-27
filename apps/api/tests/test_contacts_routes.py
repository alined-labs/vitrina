from uuid import UUID

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_compliance.api import InMemoryCompliance, record_consent
from vitrina_core.settings import Settings

SECRET = "contacts-routes-test-secret-with-32-bytes"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller_id)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def app_store() -> tuple[TestClient, InMemoryCompliance]:
    app = create_app(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            supabase_jwt_secret=SECRET,
            contact_hash_pepper="routes-test-pepper",
            contact_encryption_key=Fernet.generate_key().decode(),
        )
    )
    store: InMemoryCompliance = app.state.compliance
    return TestClient(app), store


async def test_contacts_list_with_their_consent_and_a_masked_number(
    app_store: tuple[TestClient, InMemoryCompliance],
) -> None:
    client, store = app_store
    granted = await store.resolve_contact(SELLER, "573000000001", display_name="Laura")
    await store.resolve_contact(SELLER, "573000000002")
    await store.resolve_contact(OTHER, "573000000003")
    await record_consent(store, SELLER, granted.id, prompt_text=PROMPT, response_text="sí")

    everyone = client.get("/v1/contacts", headers=bearer()).json()["items"]
    consenting = client.get("/v1/contacts?marketing_consent=granted", headers=bearer()).json()

    assert len(everyone) == 2
    assert [item["id"] for item in consenting["items"]] == [str(granted.id)]
    item = consenting["items"][0]
    assert (item["display_name"], item["phone_masked"], item["marketing_consent"]) == (
        "Laura",
        "+57 *** *** 0001",
        "granted",
    )
    assert "573000000001" not in str(everyone)


async def test_a_contact_and_its_ledger_are_the_sellers_alone(
    app_store: tuple[TestClient, InMemoryCompliance],
) -> None:
    client, store = app_store
    contact = await store.resolve_contact(SELLER, "573000000001")

    assert client.get(f"/v1/contacts/{contact.id}", headers=bearer()).status_code == 200
    assert client.get(f"/v1/contacts/{contact.id}", headers=bearer(OTHER)).status_code == 404
    assert client.get(f"/v1/contacts/{contact.id}/consents", headers=bearer(OTHER)).json() == []
    assert (
        client.post(f"/v1/contacts/{contact.id}/opt-out", headers=bearer(OTHER)).status_code == 404
    )


async def test_fr_060_a_seller_records_consent_from_a_paper_form(
    app_store: tuple[TestClient, InMemoryCompliance],
) -> None:
    client, store = app_store
    contact = await store.resolve_contact(SELLER, "573000000001")

    created = client.post(
        f"/v1/contacts/{contact.id}/consents",
        json={
            "purpose": "marketing",
            "status": "granted",
            "text_shown": "¿Acepta recibir promociones por WhatsApp?",
            "response_text": "Sí, firmado en el local",
            "channel": "in_person",
        },
        headers=bearer(),
    )
    ledger = client.get(f"/v1/contacts/{contact.id}/consents", headers=bearer()).json()

    assert created.status_code == 201
    assert (created.json()["recorded_by"], created.json()["channel"]) == ("seller", "in_person")
    assert [entry["id"] for entry in ledger] == [created.json()["id"]]


async def test_as_6_5_opting_out_from_the_dashboard_is_immediate(
    app_store: tuple[TestClient, InMemoryCompliance],
) -> None:
    client, store = app_store
    contact = await store.resolve_contact(SELLER, "573000000001")
    await record_consent(store, SELLER, contact.id, prompt_text=PROMPT, response_text="dale")

    response = client.post(f"/v1/contacts/{contact.id}/opt-out", headers=bearer())
    ledger = client.get(f"/v1/contacts/{contact.id}/consents", headers=bearer()).json()

    assert response.status_code == 200
    assert response.json()["marketing_consent"] == "revoked"
    assert response.json()["opted_out_at"] is not None
    assert [(entry["status"], entry["recorded_by"]) for entry in ledger] == [
        ("revoked", "seller"),
        ("granted", "agent"),
    ]


def test_a_manual_record_is_validated(app_store: tuple[TestClient, InMemoryCompliance]) -> None:
    client, _ = app_store

    response = client.post(
        f"/v1/contacts/{UUID(int=1)}/consents",
        json={
            "purpose": "marketing",
            "status": "maybe",
            "text_shown": "x",
            "response_text": "y",
            "channel": "whatsapp",
        },
        headers=bearer(),
    )

    assert response.status_code == 400


async def test_as_6_6_erasing_needs_the_database_and_a_known_contact(
    app_store: tuple[TestClient, InMemoryCompliance],
) -> None:
    client, store = app_store
    contact = await store.resolve_contact(SELLER, "573000000001")

    unknown = client.delete(f"/v1/contacts/{UUID(int=1)}", headers=bearer())
    no_database = client.delete(f"/v1/contacts/{contact.id}", headers=bearer())

    assert unknown.status_code == 404
    assert no_database.status_code == 503
