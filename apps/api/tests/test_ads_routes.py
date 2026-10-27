"""T042 routes without a database: each ad operation answers 503 problem+json."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings

from .test_creative_routes import ANY, SECRET, bearer


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=SECRET, storage_dir=tmp_path)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/ads", None),
        ("POST", "/v1/ads", {"creative_id": ANY, "copy_variant_id": ANY}),
        ("GET", f"/v1/ads/{ANY}", None),
        ("DELETE", f"/v1/ads/{ANY}", None),
        ("POST", f"/v1/ads/{ANY}/approve", None),
        ("GET", f"/v1/ads/{ANY}/export", None),
        ("GET", "/v1/ads/by-code/A7", None),
    ],
)
def test_ads_need_the_database(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = client.request(method, path, json=body, headers=bearer())

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
