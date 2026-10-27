from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.settings import Settings
from vitrina_evals import suites
from vitrina_evals.api import AgentTurn, TurnOutcome
from vitrina_evals.cases import fixture_id

SECRET = "evals-db-test-secret-with-32-bytes+"
SERVICE = {"X-Service-Token": "svc-token"}


def bearer() -> dict[str, str]:
    seller = UUID("00000000-0000-7000-8000-00000000000a")
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


class TruthfulAgent:
    async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
        return TurnOutcome(
            reply="La mochila azul vale $85.000.",
            cited_ids=(fixture_id("mochilas_ana", "mochila-azul"),),
        )


@pytest.mark.db
async def test_db_as_7_1_a_worker_runs_the_suite_and_the_metrics_page_shows_it(
    migrated_schema: tuple[str, str],
    db_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None,
        database_url=url,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        service_token="svc-token",
    )  # type: ignore[call-arg]
    monkeypatch.setattr(suites, "conversation_agent", TruthfulAgent)
    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/v1/evals/runs", json={"suite": "conversations_es", "sample": 1}, headers=SERVICE
        )
        assert started.status_code == 202
        run = started.json()
        assert (run["suite"], run["status"], run["replay"]) == ("conversations_es", "queued", True)

        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions), registry, sessions=db_sessions, settings=settings
        )
        assert await worker.run_once() is True

        finished = client.get(f"/v1/evals/runs/{run['id']}", headers=bearer()).json()
        listed = client.get("/v1/evals/runs", headers=bearer()).json()["items"]
        summary = client.get("/v1/metrics/summary", headers=bearer()).json()

    assert (finished["status"], finished["case_count"]) == ("passed", 1)
    assert finished["metrics"]["groundedness_rate"] == 1.0
    assert finished["metrics"]["invented_price_rate"] == 0.0
    assert finished["cases"][0]["case_id"] == "c01-precio-mochila-azul"
    assert finished["cases"][0]["passed"] is True
    assert finished["finished_at"] is not None
    assert [item["id"] for item in listed] == [run["id"]]
    assert summary["latest_eval"]["run_id"] == run["id"]
    assert summary["latest_eval"]["metrics"]["invented_price_rate"] == 0.0
