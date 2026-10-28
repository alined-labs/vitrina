import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from vitrina_core.jobs import (
    ClaimedJob,
    Job,
    JobContext,
    JobFailedError,
    TaskRegistry,
    Worker,
    backoff,
    load_tasks,
)
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import ProviderError
from vitrina_core.providers.compose import Providers, compose
from vitrina_core.settings import Settings
from vitrina_core.storage import BlobStore, LocalBlobStore

JOB = UUID("00000000-0000-7000-8000-000000000001")
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def claimed(
    task: str = "catalog.import_products", attempt: int = 1, max_attempts: int = 3
) -> ClaimedJob:
    return ClaimedJob(
        id=JOB,
        kind="product_import",
        task=task,
        payload={"image_key": "uploads/menu.jpg"},
        owner_seller_id=SELLER,
        attempt=attempt,
        max_attempts=max_attempts,
    )


@dataclass
class FakeQueue:
    pending: list[ClaimedJob] = field(default_factory=list)
    events: list[tuple[Any, ...]] = field(default_factory=list)

    async def claim(self, worker_id: str, tasks: frozenset[str]) -> ClaimedJob | None:
        for job in self.pending:
            if job.task in tasks:
                self.pending.remove(job)
                return job
        return None

    async def heartbeat(self, job_id: UUID, worker_id: str) -> None:
        self.events.append(("heartbeat", job_id))

    async def progress(self, job_id: UUID, stage: str, progress: float) -> None:
        self.events.append(("progress", stage, progress))

    async def succeed(self, job_id: UUID, result_ref: str | None) -> None:
        self.events.append(("succeed", result_ref))

    async def fail(self, job_id: UUID, error: dict[str, Any], retry_at: datetime | None) -> None:
        self.events.append(("fail", error, retry_at))

    async def requeue_stalled(self, stalled_after: timedelta) -> int:
        return 0


def worker(queue: FakeQueue, registry: TaskRegistry) -> Worker:
    return Worker(queue, registry, worker_id="mx0-test", clock=lambda: NOW)


async def test_fr_083_worker_runs_a_claimed_job_and_records_its_result() -> None:
    registry = TaskRegistry()

    @registry.task("catalog.import_products")
    async def import_products(ctx: JobContext) -> str:
        await ctx.progress("extracting", 0.5)
        return f"/v1/products?import={ctx.payload['image_key']}"

    queue = FakeQueue([claimed()])

    assert await worker(queue, registry).run_once() is True
    assert queue.events == [
        ("progress", "extracting", 0.5),
        ("succeed", "/v1/products?import=uploads/menu.jpg"),
    ]
    assert await worker(queue, registry).run_once() is False


async def test_failures_are_retried_with_backoff_until_the_last_attempt() -> None:
    registry = TaskRegistry()

    @registry.task("catalog.import_products")
    async def flaky(ctx: JobContext) -> None:
        raise ProviderError("upstream 503", provider="meta_model_api", retryable=True)

    queue = FakeQueue([claimed(attempt=1), claimed(attempt=3)])
    runner = worker(queue, registry)
    await runner.run_once()
    await runner.run_once()

    (_, first_error, first_retry), (_, last_error, last_retry) = queue.events
    assert first_retry == NOW + backoff(1)
    assert first_error["retryable"] is True
    assert last_retry is None
    assert last_error == {
        "code": "PROVIDER_UNAVAILABLE",
        "message": "upstream 503",
        "retryable": False,
    }


async def test_job_failed_error_is_final_and_keeps_its_code() -> None:
    registry = TaskRegistry()

    @registry.task("learning.generate_brief")
    async def brief(ctx: JobContext) -> None:
        raise JobFailedError("BRIEF_SCHEMA_INVALID", "the brief did not match BriefSchema")

    queue = FakeQueue([claimed(task="learning.generate_brief")])
    await worker(queue, registry).run_once()

    assert queue.events == [
        (
            "fail",
            {
                "code": "BRIEF_SCHEMA_INVALID",
                "message": "the brief did not match BriefSchema",
                "retryable": False,
            },
            None,
        )
    ]


async def test_a_job_that_exceeds_its_timeout_fails() -> None:
    registry = TaskRegistry()

    @registry.task("catalog.import_products", timeout_s=0.01)
    async def slow(ctx: JobContext) -> None:
        await asyncio.sleep(1)

    queue = FakeQueue([claimed(attempt=3)])
    await worker(queue, registry).run_once()

    assert queue.events[-1][1]["code"] == "TIMEOUT"


def test_task_names_are_unique() -> None:
    registry = TaskRegistry()
    registry.add("catalog.import_products", lambda ctx: asyncio.sleep(0))

    with pytest.raises(ValueError, match="already registered"):
        registry.add("catalog.import_products", lambda ctx: asyncio.sleep(0))


def test_backoff_doubles_from_5_seconds_and_caps_at_5_minutes() -> None:
    assert [backoff(n).total_seconds() for n in (1, 2, 3, 7, 12)] == [5, 10, 20, 300, 300]


def test_packages_register_tasks_through_entry_points() -> None:
    registry = TaskRegistry()
    point = EntryPoint(name="catalog", value=f"{__name__}:register_sample", group="vitrina.tasks")

    load_tasks(registry, [point])

    assert registry.names() == frozenset({"sample.task"})


def register_sample(registry: TaskRegistry) -> None:
    registry.add("sample.task", lambda ctx: asyncio.sleep(0))


def test_fr_083_job_view_has_the_contract_shape() -> None:
    row = {
        "id": JOB,
        "kind": "product_import",
        "status": "failed",
        "stage": None,
        "progress": 0.25,
        "result_ref": None,
        "error": {"code": "TIMEOUT", "message": "took too long", "retryable": False},
        "created_at": NOW,
        "started_at": NOW,
        "finished_at": None,
    }

    job = Job.from_row(row)

    assert job.model_dump(mode="json", exclude_unset=False) == {
        "id": str(JOB),
        "kind": "product_import",
        "status": "failed",
        "progress": 0.25,
        "result_ref": None,
        "error": {"code": "TIMEOUT", "message": "took too long", "retryable": False},
        "created_at": "2026-10-27T15:00:00Z",
        "started_at": "2026-10-27T15:00:00Z",
        "finished_at": None,
    }


async def test_a_handler_reaches_the_workers_providers(tmp_path: Path) -> None:
    providers = compose(Settings(_env_file=None, cassette_dir=tmp_path), InMemoryLedger())  # type: ignore[call-arg]
    registry = TaskRegistry()
    seen: list[Providers | None] = []

    @registry.task("catalog.import_products")
    async def import_products(ctx: JobContext) -> None:
        seen.append(ctx.providers)

    queue = FakeQueue([claimed()])
    await Worker(queue, registry, providers=providers).run_once()

    assert seen == [providers]


async def test_a_handler_reaches_the_workers_blob_store(tmp_path: Path) -> None:
    storage = LocalBlobStore(tmp_path, public_url="http://localhost:8000")
    registry = TaskRegistry()
    seen: list[BlobStore | None] = []

    @registry.task("catalog.import_products")
    async def import_products(ctx: JobContext) -> None:
        seen.append(ctx.storage)

    await Worker(FakeQueue([claimed()]), registry, storage=storage).run_once()

    assert seen == [storage]


SESSIONS: Any = object()


async def test_the_worker_runs_each_sweep_once_per_interval() -> None:
    calls: list[datetime] = []

    async def sweep(_sessions: Any, now: datetime) -> int:
        calls.append(now)
        return 1

    registry = TaskRegistry()
    registry.every("catalog.expire_reservations", every_s=60, sweep=sweep)
    clock = [1000.0]
    worker = Worker(
        FakeQueue(), registry, sessions=SESSIONS, clock=lambda: NOW, monotonic=lambda: clock[0]
    )

    await worker.run_sweeps()
    await worker.run_sweeps()
    clock[0] += 61
    await worker.run_sweeps()

    assert calls == [NOW, NOW]


async def test_a_failing_sweep_waits_for_its_next_interval() -> None:
    calls: list[int] = []

    async def broken(_sessions: Any, _now: datetime) -> int:
        calls.append(1)
        raise RuntimeError("database gone")

    registry = TaskRegistry()
    registry.every("broken", every_s=60, sweep=broken)
    worker = Worker(FakeQueue(), registry, sessions=SESSIONS, monotonic=lambda: 5.0)

    await worker.run_sweeps()
    await worker.run_sweeps()

    assert calls == [1]


async def test_sweeps_need_a_database() -> None:
    async def sweep(_sessions: Any, _now: datetime) -> int:
        raise AssertionError("must not run without a database")

    registry = TaskRegistry()
    registry.every("sweep", every_s=60, sweep=sweep)

    await Worker(FakeQueue(), registry).run_sweeps()


def test_sweep_names_are_unique() -> None:
    async def sweep(_sessions: Any, _now: datetime) -> int:
        return 0

    registry = TaskRegistry()
    registry.every("sweep", every_s=60, sweep=sweep)
    with pytest.raises(ValueError, match="already registered"):
        registry.every("sweep", every_s=60, sweep=sweep)
