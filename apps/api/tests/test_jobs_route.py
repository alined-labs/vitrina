from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.jobs import Job
from vitrina_core.settings import Settings

SECRET = "jobs-route-test-secret-with-32-bytes-plus"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
JOB = UUID("00000000-0000-7000-8000-000000000001")
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


class FakeJobs:
    async def get_for_seller(self, seller_id: UUID, job_id: UUID) -> Job | None:
        if (seller_id, job_id) != (SELLER, JOB):
            return None
        return Job(
            id=JOB,
            kind="product_import",
            status="running",
            stage="extracting",
            progress=0.5,
            created_at=NOW,
        )


def client() -> TestClient:
    app = create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET))  # type: ignore[call-arg]
    app.state.jobs = FakeJobs()
    return TestClient(app)


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {
        "sub": "subject",
        "aud": "authenticated",
        "exp": 4102444800,
        "seller_id": str(seller_id),
    }
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


def test_fr_083_a_seller_polls_its_own_job() -> None:
    response = client().get(f"/v1/jobs/{JOB}", headers=bearer())

    assert response.status_code == 200
    assert response.json() == {
        "id": str(JOB),
        "kind": "product_import",
        "status": "running",
        "stage": "extracting",
        "progress": 0.5,
        "result_ref": None,
        "error": None,
        "created_at": "2026-10-27T15:00:00Z",
        "started_at": None,
        "finished_at": None,
    }


def test_art_v_3_another_sellers_job_is_not_found() -> None:
    other = UUID("00000000-0000-7000-8000-00000000000b")

    response = client().get(f"/v1/jobs/{JOB}", headers=bearer(other))

    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


def test_polling_a_job_needs_a_seller_token() -> None:
    assert client().get(f"/v1/jobs/{JOB}").status_code == 401


def test_without_a_database_the_jobs_endpoint_says_so() -> None:
    app = create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET))  # type: ignore[call-arg]

    response = TestClient(app).get(f"/v1/jobs/{JOB}", headers=bearer())

    assert (response.status_code, response.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")
