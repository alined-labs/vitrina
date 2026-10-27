"""T034 routes without a database: each creative operation answers 503 problem+json."""

from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings

SECRET = "creative-routes-test-secret-with-32-bytes+"
SELLER = UUID("00000000-0000-7000-8000-00000000002a")
ANY = "00000000-0000-7000-8000-0000000000ff"


def bearer() -> dict[str, str]:
    claims = {
        "sub": f"subject-{SELLER}",
        "aud": "authenticated",
        "exp": 4102444800,
        "seller_id": str(SELLER),
    }
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=SECRET, storage_dir=tmp_path)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/creative/jobs", {"product_id": ANY}),
        ("GET", "/v1/creative/jobs", None),
        ("GET", f"/v1/creative/jobs/{ANY}", None),
        ("GET", f"/v1/creatives/{ANY}", None),
    ],
)
def test_creative_jobs_need_the_database(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = client.request(method, path, json=body, headers=bearer())

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_creative_jobs_need_a_signed_in_seller(client: TestClient) -> None:
    response = client.get("/v1/creative/jobs")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
