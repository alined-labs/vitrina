from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from vitrina_evals.runner import SuiteResult
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import CaseScore
from vitrina_evals.store import EvalRunStore, InMemoryEvalRuns

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def clock() -> Callable[[], datetime]:
    ticks = iter(NOW + timedelta(seconds=second) for second in range(1000))
    return lambda: next(ticks)


RESULT = SuiteResult(
    status="passed",
    metrics=EvalMetrics(groundedness_rate=1.0, invented_price_rate=0.0),
    cases=[CaseScore(case_id="c01-precio-mochila-azul", passed=True, notes="ok")],
)


@pytest.fixture
def runs() -> EvalRunStore:
    return InMemoryEvalRuns(clock=clock())


async def test_fr_070_a_run_goes_from_queued_to_its_result(runs: EvalRunStore) -> None:
    run = await runs.create("conversations_es", replay=True)
    assert (run.status, run.replay, run.finished_at) == ("queued", True, None)

    await runs.start(run.id)
    assert (await runs.get(run.id)).status == "running"  # type: ignore[union-attr]

    finished = await runs.finish(run.id, RESULT, git_sha="abc1234")

    assert finished is not None
    assert finished.status == "passed"
    assert finished.case_count == 1
    assert finished.metrics == EvalMetrics(groundedness_rate=1.0, invented_price_rate=0.0)
    assert finished.cases[0].case_id == "c01-precio-mochila-azul"  # type: ignore[index]
    assert finished.git_sha == "abc1234"
    assert finished.finished_at is not None


async def test_runs_list_newest_first_by_suite_with_cursors(runs: EvalRunStore) -> None:
    first = await runs.create("conversations_es", replay=True)
    await runs.create("fidelity", replay=True)
    third = await runs.create("conversations_es", replay=False)

    page, cursor = await runs.list_runs(limit=1, suite="conversations_es")
    rest, end = await runs.list_runs(limit=1, cursor=cursor, suite="conversations_es")

    assert [run.id for run in page] == [third.id]
    assert [run.id for run in rest] == [first.id]
    assert end is None


async def test_the_latest_finished_run_feeds_the_metrics_page(runs: EvalRunStore) -> None:
    older = await runs.create("conversations_es", replay=True)
    await runs.finish(older.id, RESULT, git_sha=None)
    newer = await runs.create("conversations_es", replay=True)
    await runs.finish(newer.id, RESULT, git_sha=None)
    await runs.create("conversations_es", replay=True)  # still queued: not a result yet

    latest = await runs.latest()

    assert latest is not None
    assert latest.id == newer.id


async def test_an_unknown_run_is_none(runs: EvalRunStore) -> None:
    run = await runs.create("conversations_es", replay=True)

    assert await runs.get(type(run.id)(int=0)) is None
    assert await runs.finish(type(run.id)(int=0), RESULT, git_sha=None) is None
