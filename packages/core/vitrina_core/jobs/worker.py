"""Task registry and worker loop (T008).

Packages register handlers through the ``vitrina.tasks`` entry point group, so adding a
task never edits a shared file:

    [project.entry-points."vitrina.tasks"]
    catalog = "vitrina_catalog.tasks:register"     # def register(registry: TaskRegistry)

A handler gets a ``JobContext`` and returns the job's ``result_ref`` (or None). Raise
``JobFailedError`` for a final failure with a stable code; its message reaches the seller as
written, so never put an exception's text in it. Any other exception, a provider outage or a
timeout is retried with backoff until ``max_attempts``, waiting longer when the provider asked
for a longer pause (``ProviderError.retry_after_s``), and the job's error says only
``JOB_FAILURE_TEXT``: the real error goes to the log under the job's id. A task with rows of its
own passes ``on_final_failure`` to close them when its job fails for good; a process that is
killed reaches no such code, so such a task also needs a sweep that reads the queue.
"""

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import EntryPoint, entry_points
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.http.context import seller_id_var
from vitrina_core.jobs.model import JOB_FAILURE_TEXT
from vitrina_core.jobs.queue import ClaimedJob, JobQueue
from vitrina_core.providers import ProviderError
from vitrina_core.providers.compose import Providers
from vitrina_core.settings import Settings, get_settings
from vitrina_core.storage import BlobStore

logger = logging.getLogger("vitrina.jobs")

TASKS_ENTRY_POINT = "vitrina.tasks"


class JobFailedError(Exception):
    """A final failure: no retry, ``code`` is shown in the job's error (e.g. BRIEF_SCHEMA_INVALID)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @classmethod
    def safe(cls, code: str) -> JobFailedError:
        """A final failure no task wording fits; raise it ``from`` the error for the log."""
        return cls(code, JOB_FAILURE_TEXT[code])


@dataclass(frozen=True)
class JobContext:
    job: ClaimedJob
    queue: JobQueue
    sessions: async_sessionmaker[AsyncSession] | None = None
    providers: Providers | None = None
    storage: BlobStore | None = None
    # Model IDs and thresholds for the task: ``ctx.settings.model_id("agent")``.
    settings: Settings = field(default_factory=get_settings)

    @property
    def job_id(self) -> UUID:
        return self.job.id

    @property
    def payload(self) -> Any:
        return self.job.payload

    @property
    def owner_seller_id(self) -> UUID | None:
        return self.job.owner_seller_id

    async def progress(self, stage: str, fraction: float) -> None:
        await self.queue.progress(self.job.id, stage, fraction)


Handler = Callable[[JobContext], Awaitable[str | None]]
# Runs once the queue has recorded a job's final failure: (ctx, the error's code).
OnFinalFailure = Callable[[JobContext, str], Awaitable[None]]
# Housekeeping the worker runs on an interval, outside the queue: (sessions, now) -> rows changed.
Sweep = Callable[[async_sessionmaker[AsyncSession], datetime], Awaitable[int]]


@dataclass(frozen=True)
class Task:
    name: str
    handler: Handler
    timeout_s: float = 600.0
    on_final_failure: OnFinalFailure | None = None


@dataclass(frozen=True)
class Periodic:
    name: str
    sweep: Sweep
    every_s: float


@dataclass
class TaskRegistry:
    _tasks: dict[str, Task] = field(default_factory=dict)
    _periodic: dict[str, Periodic] = field(default_factory=dict)

    def add(
        self,
        name: str,
        handler: Handler,
        *,
        timeout_s: float = 600.0,
        on_final_failure: OnFinalFailure | None = None,
    ) -> None:
        if name in self._tasks:
            raise ValueError(f"task {name!r} is already registered")
        self._tasks[name] = Task(name, handler, timeout_s, on_final_failure)

    def task(self, name: str, *, timeout_s: float = 600.0) -> Callable[[Handler], Handler]:
        def decorate(handler: Handler) -> Handler:
            self.add(name, handler, timeout_s=timeout_s)
            return handler

        return decorate

    def every(self, name: str, *, every_s: float, sweep: Sweep) -> None:
        """A sweep the worker runs every ``every_s`` seconds; it needs the database."""
        if name in self._periodic:
            raise ValueError(f"sweep {name!r} is already registered")
        self._periodic[name] = Periodic(name, sweep, every_s)

    def periodic(self) -> tuple[Periodic, ...]:
        return tuple(self._periodic.values())

    def get(self, name: str) -> Task | None:
        return self._tasks.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tasks)

    def narrowed(self, *, only: Iterable[str] = (), exclude: Iterable[str] = ()) -> TaskRegistry:
        """What one worker process claims; with neither limit, everything.

        ``only`` makes a dedicated worker, which runs no sweeps. ``exclude`` makes the general
        worker, which keeps them, so one set of limits never runs a sweep in two processes.
        A name no package registered raises ``ValueError``: a worker waiting for a task that
        does not exist would claim nothing and say nothing.
        """
        kept, dropped = frozenset(only), frozenset(exclude)
        if kept and dropped:
            raise ValueError("give either the tasks to claim or the tasks to leave, not both")
        unknown = (kept | dropped) - self.names()
        if unknown:
            raise ValueError(
                f"unknown task names: {', '.join(sorted(unknown))}; "
                f"registered: {', '.join(sorted(self.names()))}"
            )
        chosen = kept or self.names() - dropped
        if not chosen:
            raise ValueError("these limits leave no task left for this worker to claim")
        return TaskRegistry(
            {name: task for name, task in self._tasks.items() if name in chosen},
            {} if kept else dict(self._periodic),
        )


def load_tasks(registry: TaskRegistry, points: Iterable[EntryPoint] | None = None) -> None:
    for point in points if points is not None else entry_points(group=TASKS_ENTRY_POINT):
        point.load()(registry)


def backoff(attempt: int) -> timedelta:
    """5 s, 10 s, 20 s … capped at 5 minutes."""
    return timedelta(seconds=min(5 * 2 ** (attempt - 1), 300))


# A running job without a heartbeat for this long is requeued; health uses it for the
# longest a due job may wait (GET /health/providers).
STALLED_AFTER_S = 90.0


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        registry: TaskRegistry,
        *,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        providers: Providers | None = None,
        storage: BlobStore | None = None,
        settings: Settings | None = None,
        worker_id: str | None = None,
        poll_interval_s: float = 0.5,
        heartbeat_s: float = 15.0,
        stalled_after_s: float = STALLED_AFTER_S,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.sessions = sessions
        self.providers = providers
        self.storage = storage
        self.settings = settings or get_settings()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.poll_interval_s = poll_interval_s
        self.heartbeat_s = heartbeat_s
        self.stalled_after_s = stalled_after_s
        self.clock = clock
        self.monotonic = monotonic
        self._next_sweep: dict[str, float] = {}

    async def run(self, stop: asyncio.Event) -> None:
        """Process jobs one at a time until ``stop`` is set."""
        next_sweep = 0.0
        while not stop.is_set():
            ran = False
            try:
                if time.monotonic() >= next_sweep:
                    await self.queue.requeue_stalled(
                        timedelta(seconds=self.stalled_after_s), self.registry.names()
                    )
                    next_sweep = time.monotonic() + self.stalled_after_s / 3
                await self.run_sweeps()
                ran = await self.run_once()
            except Exception:
                logger.exception("worker loop error; retrying after the poll interval")
            if not ran:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.poll_interval_s)

    async def run_sweeps(self) -> None:
        """Each due sweep once; one that fails is logged and waits for its next interval."""
        if self.sessions is None:
            return
        for item in self.registry.periodic():
            now = self.monotonic()
            if now < self._next_sweep.get(item.name, now):
                continue
            self._next_sweep[item.name] = now + item.every_s
            try:
                changed = await item.sweep(self.sessions, self.clock())
            except Exception:
                logger.exception("sweep %s failed; next try in %g s", item.name, item.every_s)
                continue
            if changed:
                logger.info("sweep %s changed %d rows", item.name, changed)

    async def run_once(self) -> bool:
        job = await self.queue.claim(self.worker_id, self.registry.names())
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _execute(self, job: ClaimedJob) -> None:
        task = self.registry.get(job.task)
        if task is None:
            await self.queue.fail(
                job.id,
                {
                    "code": "UNKNOWN_TASK",
                    "message": JOB_FAILURE_TEXT["UNKNOWN_TASK"],
                    "retryable": False,
                },
                None,
            )
            logger.error("job %s: no handler for %s", job.id, job.task)
            return
        beat = asyncio.create_task(self._heartbeat(job.id))
        seller_token = seller_id_var.set(job.owner_seller_id)
        try:
            result = await asyncio.wait_for(
                task.handler(self._context(job)),
                task.timeout_s,
            )
        except JobFailedError as error:
            if error.__cause__ is not None:
                logger.warning("job %s (%s) %s: %r", job.id, job.task, error.code, error.__cause__)
            await self._give_up(
                job, task, {"code": error.code, "message": error.message, "retryable": False}
            )
        except TimeoutError:
            await self._failed(
                job, task, "TIMEOUT", f"{job.task} ran longer than {task.timeout_s:g} s"
            )
        except ProviderError as error:
            if error.retryable:
                await self._failed(
                    job, task, "PROVIDER_UNAVAILABLE", str(error), not_before_s=error.retry_after_s
                )
            else:
                logger.warning("job %s (%s) PROVIDER_ERROR: %s", job.id, job.task, error)
                await self._give_up(
                    job,
                    task,
                    {
                        "code": "PROVIDER_ERROR",
                        "message": JOB_FAILURE_TEXT["PROVIDER_ERROR"],
                        "retryable": False,
                    },
                )
        except Exception as error:
            logger.exception("job %s (%s) failed", job.id, job.task)
            await self._failed(job, task, "INTERNAL_ERROR", f"{type(error).__name__}: {error}")
        else:
            await self.queue.succeed(job.id, result)
        finally:
            seller_id_var.reset(seller_token)
            beat.cancel()

    def _context(self, job: ClaimedJob) -> JobContext:
        return JobContext(
            job, self.queue, self.sessions, self.providers, self.storage, self.settings
        )

    async def _failed(
        self,
        job: ClaimedJob,
        task: Task,
        code: str,
        detail: str,
        *,
        not_before_s: float | None = None,
    ) -> None:
        logger.warning("job %s (%s) %s: %s", job.id, job.task, code, detail)
        retry = job.attempt < job.max_attempts
        error = {"code": code, "message": JOB_FAILURE_TEXT[code], "retryable": retry}
        if retry:
            wait = max(backoff(job.attempt), timedelta(seconds=not_before_s or 0))
            await self.queue.fail(job.id, error, self.clock() + wait)
        else:
            await self._give_up(job, task, error)

    async def _give_up(self, job: ClaimedJob, task: Task, error: dict[str, Any]) -> None:
        """The queue first, so a notice that fails leaves a failure a sweep can still read."""
        await self.queue.fail(job.id, error, None)
        if task.on_final_failure is None:
            return
        try:
            await task.on_final_failure(self._context(job), str(error["code"]))
        except Exception:
            logger.exception("job %s (%s): closing its failed rows failed", job.id, job.task)

    async def _heartbeat(self, job_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            with contextlib.suppress(Exception):
                await self.queue.heartbeat(job_id, self.worker_id)
