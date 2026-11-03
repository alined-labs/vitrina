"""T034 routes without a database: each creative operation answers 503 problem+json."""

from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_api.routers import creative
from vitrina_core.http import ProblemCode, ProblemError
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


class OneJob:
    """The stored job a route reads back after queueing it."""

    async def get(self, seller_id: UUID, job_id: UUID) -> dict[str, Any]:
        return {"id": job_id}


@pytest.fixture
def started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[UUID]]:
    """A client whose route reaches ``start_job`` without Postgres: refuses product ``ANY``."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        supabase_jwt_secret=SECRET,
        storage_dir=tmp_path,
        seller_daily_creative_jobs=1,
    )
    app = create_app(settings)
    app.state.jobs = object()
    app.dependency_overrides[creative.job_store] = OneJob
    queued: list[UUID] = []

    async def start_job(seller_id: UUID, params: Any, **_: Any) -> UUID:
        if str(params.product_id) == ANY:
            raise ProblemError(ProblemCode.NOT_FOUND, detail="no product")
        queued.append(params.product_id)
        return params.product_id

    async def job_view(row: Any) -> Any:
        return creative.CreativeJob(
            id=row["id"],
            seller_id=SELLER,
            product_id=row["id"],
            status="queued",
            stage="queued",
            progress=0,
            created_at="2026-11-01T00:00:00Z",
        )

    monkeypatch.setattr(creative, "start_job", start_job)
    monkeypatch.setattr(creative, "job_view", job_view)
    return TestClient(app), queued


def test_as_2_1_a_seller_over_the_daily_job_quota_queues_nothing(
    started: tuple[TestClient, list[UUID]],
) -> None:
    client, queued = started
    product = "00000000-0000-7000-8000-0000000000aa"

    first = client.post("/v1/creative/jobs", json={"product_id": product}, headers=bearer())
    over = client.post("/v1/creative/jobs", json={"product_id": product}, headers=bearer())

    assert first.status_code == 202, first.text
    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"
    assert over.json()["detail"] == (
        "Llegaste al límite de hoy para crear anuncios. Vuelve a intentarlo mañana."
    )
    assert int(over.headers["Retry-After"]) > 0
    assert len(queued) == 1


def test_as_2_1_a_refused_job_gives_its_place_back(
    started: tuple[TestClient, list[UUID]],
) -> None:
    client, queued = started
    product = "00000000-0000-7000-8000-0000000000aa"

    missing = client.post("/v1/creative/jobs", json={"product_id": ANY}, headers=bearer())
    accepted = client.post("/v1/creative/jobs", json={"product_id": product}, headers=bearer())

    assert (missing.status_code, accepted.status_code) == (404, 202)
    assert len(queued) == 1
