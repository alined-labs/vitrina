"""Replay router (T080; AS-8.1, FR-071): list the fixtures, start a session.

Without a database the fixtures are listed but a session cannot start: it is a job, and jobs
live in Postgres (503, as for eval runs). CI's db job starts one for real.
"""

from uuid import UUID

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_core.settings import Settings

JWT_SECRET = "replay-routes-test-secret-with-32b"
SELLER = UUID("00000000-0000-7000-8000-0000000000d1")


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller_id)}
    return {"Authorization": f"Bearer {jwt.encode(claims, JWT_SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=JWT_SECRET)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


def test_as_8_1_the_fixtures_are_listed(client: TestClient) -> None:
    with client:
        response = client.get("/v1/replay/fixtures", headers=bearer())

    assert response.status_code == 200
    fixtures = {item["fixture_id"]: item for item in response.json()}
    assert set(fixtures) == {
        "anuncio-a7-envio",
        "anuncio-a7-regateo",
        "consentimiento-mochila-roja",
        "fuera-de-tema-y-baja",
        "reserva-mochila",
    }
    assert fixtures["reserva-mochila"]["message_count"] == 3
    assert fixtures["reserva-mochila"]["name"] == "Pregunta y reserva"


def test_as_8_1_the_fixtures_need_a_seller(client: TestClient) -> None:
    with client:
        assert client.get("/v1/replay/fixtures").status_code == 401


def test_as_8_1_an_unknown_fixture_is_not_found(client: TestClient) -> None:
    with client:
        response = client.post(
            "/v1/replay/sessions", json={"fixture_id": "no-such-fixture"}, headers=bearer()
        )

    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


def test_fr_071_without_a_database_a_session_cannot_start(client: TestClient) -> None:
    with client:
        response = client.post(
            "/v1/replay/sessions", json={"fixture_id": "reserva-mochila"}, headers=bearer()
        )

    assert (response.status_code, response.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")


@pytest.mark.parametrize("speed", [0.05, 11])
def test_as_8_1_the_speed_stays_between_a_tenth_and_ten(client: TestClient, speed: float) -> None:
    with client:
        response = client.post(
            "/v1/replay/sessions",
            json={"fixture_id": "reserva-mochila", "speed": speed},
            headers=bearer(),
        )

    assert response.status_code == 400


@pytest.mark.db
async def test_db_as_8_1_a_session_starts_as_a_replay_job(
    migrated_schema: tuple[str, str], db_sessions: async_sessionmaker[AsyncSession]
) -> None:
    url, schema = migrated_schema
    seller = await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000101",
            city="Medellín",
        ),
        auth_subject=None,
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=url,
        db_schema=schema,
        supabase_jwt_secret=JWT_SECRET,
        contact_hash_pepper="replay-routes-test-pepper",
        contact_encryption_key=Fernet.generate_key().decode(),
    )
    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/v1/replay/sessions",
            json={"fixture_id": "reserva-mochila", "speed": 2},
            headers=bearer(seller.id),
        )
        assert started.status_code == 202
        body = started.json()
        job = client.get(f"/v1/jobs/{body['job_id']}", headers=bearer(seller.id))
        conversation = client.get(
            f"/v1/conversations/{body['conversation_id']}", headers=bearer(seller.id)
        )

    UUID(body["session_id"])
    assert job.status_code == 200
    assert (job.json()["kind"], job.json()["status"]) == ("replay", "queued")
    assert conversation.status_code == 200
