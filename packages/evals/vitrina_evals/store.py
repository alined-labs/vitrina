"""Eval runs, kept for the dashboard and the record (FR-070; data-model.md ``eval_runs``).

Runs are not seller data: one list for the whole deployment, newest first.
``InMemoryEvalRuns`` serves machines without a database; ``PostgresEvalRuns`` creates a run
inside the caller's transaction, so the run and the job that executes it commit together.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.http import decode_cursor, encode_cursor, keyset_page, next_cursor
from vitrina_core.ids import new_id
from vitrina_evals.runner import SuiteResult
from vitrina_evals.schemas import EvalCaseResult, EvalMetrics, EvalRun, EvalSuite
from vitrina_evals.tables import eval_runs

FINISHED = ("passed", "failed", "error")


class EvalRunStore(Protocol):
    async def create(
        self, suite: EvalSuite, *, replay: bool, session: AsyncSession | None = None
    ) -> EvalRun: ...

    async def get(self, run_id: UUID) -> EvalRun | None: ...

    async def list_runs(
        self, *, limit: int, cursor: str | None = None, suite: EvalSuite | None = None
    ) -> tuple[list[EvalRun], str | None]: ...

    async def start(self, run_id: UUID) -> None: ...

    async def finish(
        self, run_id: UUID, result: SuiteResult, *, git_sha: str | None
    ) -> EvalRun | None: ...

    async def latest(self) -> EvalRun | None: ...


def _finished_values(result: SuiteResult, git_sha: str | None, now: datetime) -> dict[str, Any]:
    return {
        "status": result.status,
        "case_count": len(result.cases),
        "metrics": result.metrics.model_dump(mode="json"),
        "cases": [score.result().model_dump(mode="json") for score in result.cases],
        "git_sha": git_sha,
        "finished_at": now,
    }


def _view(row: Mapping[Any, Any]) -> EvalRun:
    optional: dict[str, Any] = {}
    if row.get("git_sha"):
        optional["git_sha"] = row["git_sha"]
    if row["status"] in FINISHED:
        optional["case_count"] = row["case_count"]
        optional["metrics"] = EvalMetrics.model_validate(row["metrics"] or {})
        optional["cases"] = [EvalCaseResult.model_validate(case) for case in row["cases"] or []]
    return EvalRun(
        id=row["id"],
        suite=row["suite"],
        replay=row["replay"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        **optional,
    )


@dataclass
class InMemoryEvalRuns:
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    rows: dict[UUID, dict[str, Any]] = field(default_factory=dict)

    async def create(
        self, suite: EvalSuite, *, replay: bool, session: AsyncSession | None = None
    ) -> EvalRun:
        now = self.clock()
        row: dict[str, Any] = {
            "id": new_id(),
            "suite": suite,
            "replay": replay,
            "status": "queued",
            "case_count": 0,
            "metrics": {},
            "cases": [],
            "git_sha": None,
            "started_at": now,
            "finished_at": None,
            "created_at": now,
        }
        self.rows[row["id"]] = row
        return _view(row)

    async def get(self, run_id: UUID) -> EvalRun | None:
        row = self.rows.get(run_id)
        return _view(row) if row is not None else None

    async def list_runs(
        self, *, limit: int, cursor: str | None = None, suite: EvalSuite | None = None
    ) -> tuple[list[EvalRun], str | None]:
        rows = sorted(
            (row for row in self.rows.values() if suite is None or row["suite"] == suite),
            key=lambda row: (row["created_at"], row["id"]),
            reverse=True,
        )
        if cursor is not None:
            position = decode_cursor(cursor)
            rows = [row for row in rows if (row["created_at"], row["id"]) < position]
        page = rows[:limit]
        more = len(rows) > limit
        last = page[-1] if page else None
        token = encode_cursor(last["created_at"], last["id"]) if more and last else None
        return [_view(row) for row in page], token

    async def start(self, run_id: UUID) -> None:
        if run_id in self.rows:
            self.rows[run_id].update(status="running", started_at=self.clock())

    async def finish(
        self, run_id: UUID, result: SuiteResult, *, git_sha: str | None
    ) -> EvalRun | None:
        row = self.rows.get(run_id)
        if row is None:
            return None
        row.update(_finished_values(result, git_sha, self.clock()))
        return _view(row)

    async def latest(self) -> EvalRun | None:
        finished = [row for row in self.rows.values() if row["status"] in FINISHED]
        if not finished:
            return None
        return _view(max(finished, key=lambda row: (row["finished_at"], row["id"])))


class PostgresEvalRuns:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create(
        self, suite: EvalSuite, *, replay: bool, session: AsyncSession | None = None
    ) -> EvalRun:
        statement = (
            sa.insert(eval_runs)
            .values(id=new_id(), suite=suite, replay=replay, status="queued")
            .returning(*eval_runs.c)
        )
        if session is not None:
            row = (await session.execute(statement)).mappings().one()
        else:
            async with self.sessions() as own, own.begin():
                row = (await own.execute(statement)).mappings().one()
        return _view(row)

    async def get(self, run_id: UUID) -> EvalRun | None:
        async with self.sessions() as session:
            row = (
                (await session.execute(sa.select(eval_runs).where(eval_runs.c.id == run_id)))
                .mappings()
                .first()
            )
        return _view(row) if row is not None else None

    async def list_runs(
        self, *, limit: int, cursor: str | None = None, suite: EvalSuite | None = None
    ) -> tuple[list[EvalRun], str | None]:
        statement = sa.select(eval_runs)
        if suite is not None:
            statement = statement.where(eval_runs.c.suite == suite)
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        keyset_page(statement, eval_runs, limit=limit, cursor=cursor)
                    )
                )
                .mappings()
                .all()
            )
        page, token = next_cursor(rows, limit=limit)
        return [_view(row) for row in page], token

    async def start(self, run_id: UUID) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(eval_runs)
                .where(eval_runs.c.id == run_id)
                .values(status="running", started_at=sa.func.now())
            )

    async def finish(
        self, run_id: UUID, result: SuiteResult, *, git_sha: str | None
    ) -> EvalRun | None:
        values = _finished_values(result, git_sha, datetime.now(UTC))
        async with self.sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        sa.update(eval_runs)
                        .where(eval_runs.c.id == run_id)
                        .values(**values)
                        .returning(*eval_runs.c)
                    )
                )
                .mappings()
                .first()
            )
        return _view(row) if row is not None else None

    async def latest(self) -> EvalRun | None:
        statement = (
            sa.select(eval_runs)
            .where(eval_runs.c.status.in_(FINISHED))
            .order_by(eval_runs.c.finished_at.desc(), eval_runs.c.id.desc())
            .limit(1)
        )
        async with self.sessions() as session:
            row = (await session.execute(statement)).mappings().first()
        return _view(row) if row is not None else None
