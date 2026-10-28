"""T064 routes without a database: campaigns live in Postgres, so each one answers 503."""

from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings

SECRET = "reactivation-routes-secret-with-32-bytes"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
CAMPAIGN = "00000000-0000-7000-8000-0000000000c9"


def bearer() -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(SELLER)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/reactivation/campaigns", {"template_name": "vitrina_reactivacion"}),
        ("get", "/v1/reactivation/campaigns", None),
        ("get", f"/v1/reactivation/campaigns/{CAMPAIGN}", None),
        ("post", f"/v1/reactivation/campaigns/{CAMPAIGN}/send", None),
        ("post", "/v1/reactivation/eligibility", {"contact_id": CAMPAIGN}),
    ],
)
def test_as_6_7_campaigns_need_the_database(method: str, path: str, body: object) -> None:
    client = TestClient(create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET)))  # type: ignore[call-arg]

    answer = client.request(method, path, json=body, headers=bearer())

    assert (answer.status_code, answer.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")


def test_as_6_7_campaigns_need_a_seller() -> None:
    client = TestClient(create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET)))  # type: ignore[call-arg]

    answer = client.get("/v1/reactivation/campaigns")

    assert answer.status_code == 401
