from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings

SECRET = "health-routes-test-secret-with-32-bytes"


def bearer() -> dict[str, str]:
    seller = UUID("00000000-0000-7000-8000-00000000000a")
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET)))  # type: ignore[call-arg]


def test_nfr_02_provider_health_needs_a_seller(client: TestClient) -> None:
    assert client.get("/v1/health/providers").status_code == 401


def test_nfr_02_replay_mode_without_a_database_reports_every_row_mocked(
    client: TestClient,
) -> None:
    response = client.get("/v1/health/providers", headers=bearer())

    assert response.status_code == 200
    rows = response.json()
    assert {name: row["status"] for name, row in rows.items()} == {
        "meta_model_api": "mocked",
        "whatsapp": "mocked",
        "database": "mocked",
        "queue": "mocked",
    }
    assert all("checked_at" in row and "latency_ms" not in row for row in rows.values())


@pytest.mark.db
def test_db_nfr_02_the_database_and_the_queue_answer(migrated_schema: tuple[str, str]) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None, database_url=url, db_schema=schema, supabase_jwt_secret=SECRET
    )  # type: ignore[call-arg]

    with TestClient(create_app(settings)) as client:
        rows = client.get("/v1/health/providers", headers=bearer()).json()

    assert (rows["database"]["status"], rows["queue"]["status"]) == ("ok", "ok")
    assert "detail" not in rows["queue"]  # counts span every seller
    assert rows["meta_model_api"]["status"] == "mocked"
