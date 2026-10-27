from datetime import UTC, datetime
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.api import InMemoryCatalog
from vitrina_core.settings import Settings
from vitrina_core.storage import blob_key
from vitrina_creative.api import AssetRecord, assets_from_state

SECRET = "catalog-routes-test-secret-with-32-bytes+"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {
        "sub": "subject",
        "aud": "authenticated",
        "exp": 4102444800,
        "seller_id": str(seller_id),
    }
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    app = create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET))  # type: ignore[call-arg]
    app.state.catalog = InMemoryCatalog()
    return TestClient(app)


def test_as_1_2_post_products_returns_201_with_an_active_product(client: TestClient) -> None:
    response = client.post(
        "/v1/products",
        json={
            "name": "Bolso tejido",
            "price_cop": 85000,
            "stock": 3,
            "attributes": {"color": "rojo"},
        },
        headers={**bearer(), "Idempotency-Key": "create-bolso-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert {
        key: body[key] for key in ("name", "price_cop", "stock", "reserved", "status", "source")
    } == {
        "name": "Bolso tejido",
        "price_cop": 85000,
        "stock": 3,
        "reserved": 0,
        "status": "active",
        "source": "manual",
    }
    assert body["seller_id"] == str(SELLER)
    assert body["attributes"] == {"color": "rojo"}


def test_products_read_update_archive_and_404(client: TestClient) -> None:
    created = client.post(
        "/v1/products", json={"name": "Gorra", "price_cop": 30000}, headers=bearer()
    ).json()
    url = f"/v1/products/{created['id']}"

    assert client.get(url, headers=bearer()).json()["name"] == "Gorra"
    assert (
        client.patch(url, json={"price_cop": 32000}, headers=bearer()).json()["price_cop"] == 32000
    )
    assert client.delete(url, headers=bearer()).status_code == 204
    assert client.get(url, headers=bearer()).json()["status"] == "archived"
    missing = client.get(f"/v1/products/{OTHER}", headers=bearer())
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")


def test_art_v_3_another_seller_cannot_see_or_change_a_product(client: TestClient) -> None:
    created = client.post(
        "/v1/products", json={"name": "Gorra", "price_cop": 30000}, headers=bearer()
    ).json()
    url = f"/v1/products/{created['id']}"

    assert client.get(url, headers=bearer(OTHER)).status_code == 404
    assert client.patch(url, json={"name": "mía"}, headers=bearer(OTHER)).status_code == 404
    assert client.delete(url, headers=bearer(OTHER)).status_code == 404


def test_list_products_pages_and_filters(client: TestClient) -> None:
    for name in ("Bolso tejido", "Gorra", "Bolso de cuero"):
        client.post("/v1/products", json={"name": name, "price_cop": 10000}, headers=bearer())

    first = client.get("/v1/products", params={"limit": 2}, headers=bearer()).json()
    second = client.get(
        "/v1/products", params={"limit": 2, "cursor": first["next_cursor"]}, headers=bearer()
    ).json()
    bags = client.get("/v1/products", params={"q": "bolso"}, headers=bearer()).json()

    assert [item["name"] for item in first["items"]] == ["Bolso de cuero", "Gorra"]
    assert ([item["name"] for item in second["items"]], second["next_cursor"]) == (
        ["Bolso tejido"],
        None,
    )
    assert {item["name"] for item in bags["items"]} == {"Bolso tejido", "Bolso de cuero"}


def test_product_validation_errors_are_400_problems(client: TestClient) -> None:
    response = client.post(
        "/v1/products", json={"name": "Gorra", "price_cop": -1}, headers=bearer()
    )

    assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_ERROR")


def test_as_1_4_post_facts_stores_a_shipping_fact_with_its_amounts(client: TestClient) -> None:
    response = client.post(
        "/v1/facts",
        json={"kind": "shipping", "text": "Envío a Medellín $12.000, 2–3 días"},
        headers=bearer(),
    )

    assert response.status_code == 201
    fact = response.json()
    assert (fact["kind"], fact["status"], fact["amounts_cop"]) == ("shipping", "active", [12000])
    listed = client.get("/v1/facts", params={"kind": "shipping"}, headers=bearer()).json()
    assert [item["id"] for item in listed["items"]] == [fact["id"]]


def test_facts_update_and_delete(client: TestClient) -> None:
    fact = client.post(
        "/v1/facts", json={"kind": "faq", "text": "Abrimos de 9 a 6"}, headers=bearer()
    ).json()
    url = f"/v1/facts/{fact['id']}"

    assert client.patch(url, json={"status": "draft"}, headers=bearer()).json()["status"] == "draft"
    assert client.delete(url, headers=bearer()).status_code == 204
    assert client.delete(url, headers=bearer()).status_code == 404


def test_catalog_routes_need_a_seller_token(client: TestClient) -> None:
    assert client.get("/v1/products").status_code == 401
    assert client.post("/v1/facts", json={"kind": "faq", "text": "Hola"}).status_code == 401


PNG = b"\x89PNG\r\n\x1a\n synthetic menu photo"


def test_as_1_3_import_refuses_files_that_are_not_photos(client: TestClient) -> None:
    response = client.post(
        "/v1/products/import",
        files={"image": ("menu.pdf", b"%PDF-1.7 not a photo", "image/jpeg")},
        headers=bearer(),
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [
        {"field": "image", "message": "send a JPEG, PNG or WebP photo"}
    ]


def test_fr_010_import_refuses_photos_over_10_mb(client: TestClient) -> None:
    big = PNG + b"\x00" * (10 * 1024 * 1024)

    response = client.post(
        "/v1/products/import", files={"image": ("menu.png", big, "image/png")}, headers=bearer()
    )

    assert response.status_code == 400
    assert response.json()["errors"][0]["message"] == "the photo is larger than 10 MB"


def test_as_1_3_import_validates_the_hint(client: TestClient) -> None:
    response = client.post(
        "/v1/products/import",
        files={"image": ("menu.png", PNG, "image/png")},
        data={"hint": "x" * 301},
        headers=bearer(),
    )

    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "hint"


def test_as_1_3_import_without_a_database_says_so(client: TestClient) -> None:
    response = client.post(
        "/v1/products/import", files={"image": ("menu.png", PNG, "image/png")}, headers=bearer()
    )

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_import_needs_a_seller(client: TestClient) -> None:
    response = client.post("/v1/products/import", files={"image": ("menu.png", PNG, "image/png")})

    assert response.status_code == 401


async def test_a_product_lists_its_assets_oldest_first(client: TestClient) -> None:
    created = client.post(
        "/v1/products", json={"name": "Bolso tejido", "price_cop": 85000}, headers=bearer()
    ).json()
    product_id = UUID(created["id"])
    store = assets_from_state(client.app.state)  # type: ignore[attr-defined]
    older, newer = (
        AssetRecord(
            id=UUID(int=index),
            seller_id=SELLER,
            product_id=product_id,
            kind="image",
            storage_key=blob_key(SELLER, "assets", UUID(int=index), "jpg"),
            sha256=str(index) * 64,
            created_at=datetime(2026, 10, 27, 15, index, tzinfo=UTC),
        )
        for index in (1, 2)
    )
    await store.add(newer)
    await store.add(older)

    one = client.get(f"/v1/products/{product_id}", headers=bearer()).json()
    listed = client.get("/v1/products", headers=bearer()).json()["items"]
    other_seller = client.get(f"/v1/products/{product_id}", headers=bearer(OTHER))

    assert created["asset_ids"] == []
    assert one["asset_ids"] == [str(older.id), str(newer.id)]
    assert listed[0]["asset_ids"] == [str(older.id), str(newer.id)]
    assert other_seller.status_code == 404
