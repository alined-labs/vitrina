import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_evals.runner import SuiteResult
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import CaseScore
from vitrina_evals.store import PostgresEvalRuns

RESULT = SuiteResult(
    status="failed",
    metrics=EvalMetrics(invented_price_rate=0.5),
    cases=[
        CaseScore(case_id="c01", passed=True, notes="ok"),
        CaseScore(
            case_id="c10", passed=False, notes="states amounts no cited item supports: 60000"
        ),
    ],
)


@pytest.mark.db
async def test_db_fr_070_runs_persist_their_result_and_list_newest_first(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    runs = PostgresEvalRuns(db_sessions)
    async with db_sessions() as session, session.begin():
        first = await runs.create("conversations_es", replay=True, session=session)
    second = await runs.create("fidelity", replay=False)

    await runs.start(first.id)
    running = await runs.get(first.id)
    finished = await runs.finish(first.id, RESULT, git_sha="abc1234")
    page, cursor = await runs.list_runs(limit=1)
    rest, end = await runs.list_runs(limit=1, cursor=cursor)
    only_fidelity, _ = await runs.list_runs(limit=10, suite="fidelity")

    assert running is not None
    assert running.status == "running"
    assert finished is not None
    assert (finished.status, finished.case_count, finished.git_sha) == ("failed", 2, "abc1234")
    assert finished.metrics == EvalMetrics(invented_price_rate=0.5)
    assert [case.case_id for case in finished.cases] == ["c01", "c10"]  # type: ignore[union-attr]
    assert [run.id for run in page + rest] == [second.id, first.id]
    assert end is None
    assert [run.id for run in only_fidelity] == [second.id]
    latest = await runs.latest()
    assert latest is not None
    assert latest.id == first.id
