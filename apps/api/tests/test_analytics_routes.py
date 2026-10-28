from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings

SECRET = "analytics-routes-test-secret-with-32-bytes"


def bearer() -> dict[str, str]:
    seller = UUID("00000000-0000-7000-8000-00000000000a")
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET)))  # type: ignore[call-arg]


@pytest.mark.parametrize("path", ["/v1/analytics/attribution", "/v1/analytics/objections"])
def test_analytics_needs_a_seller(client: TestClient, path: str) -> None:
    assert client.get(path, params={"from": "2026-10-01", "to": "2026-10-28"}).status_code == 401


@pytest.mark.parametrize("path", ["/v1/analytics/attribution", "/v1/analytics/objections"])
def test_a_period_that_ends_before_it_starts_is_refused(client: TestClient, path: str) -> None:
    response = client.get(path, params={"from": "2026-10-28", "to": "2026-10-01"}, headers=bearer())

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_as_4_1_without_a_database_the_reports_are_empty(client: TestClient) -> None:
    period = {"from": "2026-10-01", "to": "2026-10-28"}

    attribution = client.get("/v1/analytics/attribution", params=period, headers=bearer())
    objections = client.get("/v1/analytics/objections", params=period, headers=bearer())

    assert attribution.status_code == 200
    assert attribution.json() == {
        "period_from": "2026-10-01",
        "period_to": "2026-10-28",
        "ads": [],
        "by_angle": {},
    }
    assert objections.json() == {
        "period_from": "2026-10-01",
        "period_to": "2026-10-28",
        "totals": {},
        "by_ad": [],
    }
