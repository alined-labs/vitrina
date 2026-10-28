"""The Postgres queue on the ``jobs`` table (ADR-0002: one datastore, transactional enqueue).

``enqueue`` runs in the caller's session, so the job commits or rolls back with the rows
that caused it. Workers claim one job at a time with ``FOR UPDATE SKIP LOCKED``; a job whose
worker stops heart-beating goes back to the queue.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import jobs
from vitrina_core.ids import new_id
from vitrina_core.jobs.model import API_KINDS, Job, QueueKind


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    kind: str
    task: str
    payload: Mapping[str, Any]
    owner_seller_id: UUID | None
    attempt: int
    max_attempts: int


class JobQueue(Protocol):
    """What the worker needs from a queue."""

    async def claim(self, worker_id: str, tasks: frozenset[str]) -> ClaimedJob | None: ...

    async def heartbeat(self, job_id: UUID, worker_id: str) -> None: ...

    async def progress(self, job_id: UUID, stage: str, progress: float) -> None: ...

    async def succeed(self, job_id: UUID, result_ref: str | None) -> None: ...

    async def fail(
        self, job_id: UUID, error: dict[str, Any], retry_at: datetime | None
    ) -> None: ...

    async def requeue_stalled(self, stalled_after: timedelta) -> int: ...


class JobReader(Protocol):
    """What GET /jobs/{job_id} needs."""

    async def get_for_seller(self, seller_id: UUID, job_id: UUID) -> Job | None: ...


class PostgresJobQueue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        kind: QueueKind,
        task: str,
        payload: Mapping[str, Any] | None = None,
        owner_seller_id: UUID | None = None,
        idempotency_key: str | None = None,
        run_after: datetime | None = None,
        max_attempts: int = 3,
    ) -> UUID:
        """Insert a queued job inside ``session``'s transaction; a repeated key returns the first."""
        values: dict[str, Any] = {
            "id": new_id(),
            "kind": kind,
            "task": task,
            "payload": dict(payload or {}),
            "owner_seller_id": owner_seller_id,
            "idempotency_key": idempotency_key,
            "max_attempts": max_attempts,
        }
        if run_after is not None:
            values["run_after"] = run_after
        inserted = await session.scalar(
            insert(jobs)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[jobs.c.task, jobs.c.idempotency_key],
                index_where=jobs.c.idempotency_key.isnot(None),
            )
            .returning(jobs.c.id)
        )
        if inserted is not None:
            return UUID(str(inserted))
        existing = await session.scalar(
            sa.select(jobs.c.id).where(
                jobs.c.task == task, jobs.c.idempotency_key == idempotency_key
            )
        )
        return UUID(str(existing))

    async def claim(self, worker_id: str, tasks: frozenset[str]) -> ClaimedJob | None:
        candidate = (
            sa.select(jobs.c.id)
            .where(
                jobs.c.status == "queued",
                jobs.c.run_after <= sa.func.now(),
                jobs.c.task.in_(sorted(tasks)),
            )
            .order_by(jobs.c.run_after, jobs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        async with self.sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        sa.update(jobs)
                        .where(jobs.c.id == candidate)
                        .values(
                            status="running",
                            attempts=jobs.c.attempts + 1,
                            started_at=sa.func.coalesce(jobs.c.started_at, sa.func.now()),
                            locked_by=worker_id,
                            heartbeat_at=sa.func.now(),
                        )
                        .returning(*jobs.c)
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return ClaimedJob(
            id=row["id"],
            kind=row["kind"],
            task=row["task"],
            payload=row["payload"],
            owner_seller_id=row["owner_seller_id"],
            attempt=row["attempts"],
            max_attempts=row["max_attempts"],
        )

    async def _set(self, job_id: UUID, **values: Any) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(sa.update(jobs).where(jobs.c.id == job_id).values(**values))

    async def heartbeat(self, job_id: UUID, worker_id: str) -> None:
        await self._set(job_id, heartbeat_at=sa.func.now(), locked_by=worker_id)

    async def progress(self, job_id: UUID, stage: str, progress: float) -> None:
        await self._set(job_id, stage=stage, progress=max(0.0, min(progress, 1.0)))

    async def succeed(self, job_id: UUID, result_ref: str | None) -> None:
        await self._set(
            job_id,
            status="succeeded",
            progress=1,
            result_ref=result_ref,
            error=None,
            finished_at=sa.func.now(),
            locked_by=None,
        )

    async def fail(self, job_id: UUID, error: dict[str, Any], retry_at: datetime | None) -> None:
        if retry_at is not None:
            await self._set(
                job_id, status="queued", error=error, run_after=retry_at, locked_by=None
            )
        else:
            await self._set(
                job_id, status="failed", error=error, finished_at=sa.func.now(), locked_by=None
            )

    async def backlog(self) -> tuple[int, float]:
        """Due jobs nobody has claimed yet: how many, and the longest wait in seconds."""
        waited = sa.func.extract("epoch", sa.func.now() - jobs.c.run_after)
        async with self.sessions() as session:
            due, longest = (
                await session.execute(
                    sa.select(sa.func.count(), sa.func.coalesce(sa.func.max(waited), 0)).where(
                        jobs.c.status == "queued", jobs.c.run_after <= sa.func.now()
                    )
                )
            ).one()
        return int(due), float(longest)

    async def requeue_stalled(self, stalled_after: timedelta) -> int:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                sa.update(jobs)
                .where(
                    jobs.c.status == "running", jobs.c.heartbeat_at < sa.func.now() - stalled_after
                )
                .values(status="queued", locked_by=None, run_after=sa.func.now())
                .returning(jobs.c.id)
            )
            return len(result.all())

    async def get_for_seller(self, seller_id: UUID, job_id: UUID) -> Job | None:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(jobs).where(
                            jobs.c.id == job_id,
                            jobs.c.owner_seller_id == seller_id,
                            jobs.c.kind.in_(sorted(API_KINDS)),
                        )
                    )
                )
                .mappings()
                .first()
            )
        return Job.from_row(row) if row is not None else None
