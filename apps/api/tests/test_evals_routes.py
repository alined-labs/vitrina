from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.settings import Settings
from vitrina_evals.api import EvalMetrics, InMemoryEvalRuns, SuiteResult

SECRET = "evals-routes-test-secret-with-32-bytes+"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")


def bearer() -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(SELLER)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def runs() -> InMemoryEvalRuns:
    return InMemoryEvalRuns()


@pytest.fixture
def client(runs: InMemoryEvalRuns) -> TestClient:
    app = create_app(
        Settings(_env_file=None, supabase_jwt_secret=SECRET, service_token="svc-token")  # type: ignore[call-arg]
    )
    app.state.eval_runs = runs
    return TestClient(app)


def test_fr_070_starting_a_run_needs_the_database(client: TestClient) -> None:
    response = client.post(
        "/v1/evals/runs",
        json={"suite": "conversations_es"},
        headers={"X-Service-Token": "svc-token"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_starting_a_run_needs_a_worker_or_a_seller(client: TestClient) -> None:
    assert client.post("/v1/evals/runs", json={"suite": "conversations_es"}).status_code == 401


def test_a_run_request_is_validated(client: TestClient) -> None:
    response = client.post(
        "/v1/evals/runs",
        json={"suite": "conversations_es", "sample": 0},
        headers={"X-Service-Token": "svc-token"},
    )

    assert response.status_code == 400


async def test_fr_070_runs_are_listed_and_read_with_their_metrics(
    client: TestClient, runs: InMemoryEvalRuns
) -> None:
    run = await runs.create("conversations_es", replay=True)
    result = SuiteResult("passed", EvalMetrics(groundedness_rate=1.0, invented_price_rate=0.0), [])
    await runs.finish(run.id, result, git_sha="abc1234")

    listed = client.get("/v1/evals/runs?suite=conversations_es", headers=bearer())
    read = client.get(f"/v1/evals/runs/{run.id}", headers=bearer())
    missing = client.get(f"/v1/evals/runs/{UUID(int=0)}", headers=bearer())

    assert [item["id"] for item in listed.json()["items"]] == [str(run.id)]
    assert read.json()["status"] == "passed"
    assert read.json()["metrics"] == {"groundedness_rate": 1.0, "invented_price_rate": 0.0}
    assert missing.status_code == 404


async def test_the_metrics_page_shows_the_latest_finished_run(
    client: TestClient, runs: InMemoryEvalRuns
) -> None:
    empty = client.get("/v1/metrics/summary", headers=bearer()).json()
    run = await runs.create("conversations_es", replay=True)
    await runs.finish(
        run.id, SuiteResult("failed", EvalMetrics(invented_price_rate=0.05), []), git_sha=None
    )

    summary = client.get("/v1/metrics/summary", headers=bearer()).json()

    assert empty == {"latest_eval": {}, "live": {}}
    assert summary["latest_eval"]["run_id"] == str(run.id)
    assert summary["latest_eval"]["metrics"] == {"invented_price_rate": 0.05}
    assert summary["live"] == {}
