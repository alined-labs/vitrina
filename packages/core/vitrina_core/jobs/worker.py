"""Task registry and worker loop (T008).

Packages register handlers through the ``vitrina.tasks`` entry point group, so adding a
task never edits a shared file:

    [project.entry-points."vitrina.tasks"]
    catalog = "vitrina_catalog.tasks:register"     # def register(registry: TaskRegistry)

A handler gets a ``JobContext`` and returns the job's ``result_ref`` (or None). Raise
``JobFailedError`` for a final failure with a stable code; any other exception, a provider
outage or a timeout is retried with backoff until ``max_attempts``.
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


@dataclass(frozen=True)
class Task:
    name: str
    handler: Handler
    timeout_s: float = 600.0


@dataclass
class TaskRegistry:
    _tasks: dict[str, Task] = field(default_factory=dict)

    def add(self, name: str, handler: Handler, *, timeout_s: float = 600.0) -> None:
        if name in self._tasks:
            raise ValueError(f"task {name!r} is already registered")
        self._tasks[name] = Task(name, handler, timeout_s)

    def task(self, name: str, *, timeout_s: float = 600.0) -> Callable[[Handler], Handler]:
        def decorate(handler: Handler) -> Handler:
            self.add(name, handler, timeout_s=timeout_s)
            return handler

        return decorate

    def get(self, name: str) -> Task | None:
        return self._tasks.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tasks)


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

    async def run(self, stop: asyncio.Event) -> None:
        """Process jobs one at a time until ``stop`` is set (one dev process per machine)."""
        next_sweep = 0.0
        while not stop.is_set():
            ran = False
            try:
                if time.monotonic() >= next_sweep:
                    await self.queue.requeue_stalled(timedelta(seconds=self.stalled_after_s))
                    next_sweep = time.monotonic() + self.stalled_after_s / 3
                ran = await self.run_once()
            except Exception:
                logger.exception("worker loop error; retrying after the poll interval")
            if not ran:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.poll_interval_s)

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
                    "message": f"no handler for {job.task}",
                    "retryable": False,
                },
                None,
            )
            return
        beat = asyncio.create_task(self._heartbeat(job.id))
        seller_token = seller_id_var.set(job.owner_seller_id)
        try:
            result = await asyncio.wait_for(
                task.handler(
                    JobContext(
                        job, self.queue, self.sessions, self.providers, self.storage, self.settings
                    )
                ),
                task.timeout_s,
            )
        except JobFailedError as error:
            await self.queue.fail(
                job.id, {"code": error.code, "message": error.message, "retryable": False}, None
            )
        except TimeoutError:
            await self._failed(job, "TIMEOUT", f"{job.task} ran longer than {task.timeout_s:g} s")
        except ProviderError as error:
            if error.retryable:
                await self._failed(job, "PROVIDER_UNAVAILABLE", str(error))
            else:
                await self.queue.fail(
                    job.id,
                    {"code": "PROVIDER_ERROR", "message": str(error), "retryable": False},
                    None,
                )
        except Exception as error:
            logger.exception("job %s (%s) failed", job.id, job.task)
            await self._failed(job, "INTERNAL_ERROR", f"{type(error).__name__}: {error}")
        else:
            await self.queue.succeed(job.id, result)
        finally:
            seller_id_var.reset(seller_token)
            beat.cancel()

    async def _failed(self, job: ClaimedJob, code: str, message: str) -> None:
        retry = job.attempt < job.max_attempts
        error = {"code": code, "message": message[:500], "retryable": retry}
        await self.queue.fail(job.id, error, self.clock() + backoff(job.attempt) if retry else None)

    async def _heartbeat(self, job_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            with contextlib.suppress(Exception):
                await self.queue.heartbeat(job_id, self.worker_id)
