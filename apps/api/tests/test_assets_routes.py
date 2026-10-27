"""T030 routes: POST /assets and GET /assets/{asset_id} (FR-010), seller-scoped (Art. V.3)."""

from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.api import InMemoryCatalog
from vitrina_core.settings import Settings
from vitrina_creative.media import IMAGE_MAX_BYTES
from vitrina_creative.testing import source_photo

SECRET = "assets-routes-test-secret-with-32-bytes+"
SELLER = UUID("00000000-0000-7000-8000-00000000001a")
OTHER = UUID("00000000-0000-7000-8000-00000000001b")


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {
        "sub": f"subject-{seller_id}",
        "aud": "authenticated",
        "exp": 4102444800,
        "seller_id": str(seller_id),
    }
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=SECRET, storage_dir=tmp_path)  # type: ignore[call-arg]
    app = create_app(settings)
    app.state.catalog = InMemoryCatalog()
    return TestClient(app)


def product(client: TestClient, seller_id: UUID = SELLER) -> str:
    response = client.post(
        "/v1/products", json={"name": "Bolso tejido", "price_cop": 85000}, headers=bearer(seller_id)
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def upload(
    client: TestClient,
    product_id: str,
    data: bytes,
    name: str = "bolso.png",
    seller_id: UUID = SELLER,
):
    return client.post(
        "/v1/assets",
        files={"file": (name, data, "application/octet-stream")},
        data={"product_id": product_id},
        headers=bearer(seller_id),
    )


def test_fr_010_a_photo_upload_returns_201_with_a_signed_link(client: TestClient) -> None:
    product_id = product(client)
    data = source_photo().image.data
    response = upload(client, product_id, data)
    assert response.status_code == 201, response.json()
    asset = response.json()
    assert (asset["seller_id"], asset["product_id"], asset["kind"]) == (
        str(SELLER),
        product_id,
        "image",
    )
    assert (asset["width"], asset["height"]) == (512, 512)
    assert asset["duration_s"] is None
    assert len(asset["sha256"]) == 64
    link = urlsplit(asset["url"])
    downloaded = client.get(f"{link.path}?{link.query}")
    assert downloaded.status_code == 200
    assert downloaded.content == data


def test_get_asset_answers_the_same_asset_with_a_fresh_link(client: TestClient) -> None:
    created = upload(client, product(client), source_photo().image.data).json()
    response = client.get(f"/v1/assets/{created['id']}", headers=bearer())
    assert response.status_code == 200
    assert {k: v for k, v in response.json().items() if k != "url"} == {
        k: v for k, v in created.items() if k != "url"
    }


def test_the_same_photo_twice_is_one_asset(client: TestClient) -> None:
    product_id = product(client)
    first = upload(client, product_id, source_photo().image.data).json()
    second = upload(client, product_id, source_photo().image.data).json()
    assert first["id"] == second["id"]


def test_an_unknown_product_is_a_400_on_product_id(client: TestClient) -> None:
    response = upload(client, "00000000-0000-7000-8000-0000000000ff", source_photo().image.data)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] == "product_id"


def test_art_v_3_another_sellers_product_and_asset_are_out_of_reach(client: TestClient) -> None:
    created = upload(client, product(client), source_photo().image.data).json()
    assert client.get(f"/v1/assets/{created['id']}", headers=bearer(OTHER)).status_code == 404
    assert (
        upload(
            client, created["product_id"], source_photo().image.data, seller_id=OTHER
        ).status_code
        == 400
    )


def test_fr_010_a_file_that_is_not_a_photo_or_video_is_a_400_on_file(client: TestClient) -> None:
    response = upload(client, product(client), b"%PDF-1.7 no es una foto", name="lista.pdf")
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "file"


def test_fr_010_a_photo_over_10_mb_is_a_413(client: TestClient) -> None:
    response = upload(client, product(client), b"\x89PNG\r\n\x1a\n" + bytes(IMAGE_MAX_BYTES))
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
