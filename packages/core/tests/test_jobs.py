import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from vitrina_core.jobs import (
    JOB_FAILURE_TEXT,
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
from vitrina_core.providers import ProviderError, RateLimitedError
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
    requeued_for: list[frozenset[str]] = field(default_factory=list)

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

    async def requeue_stalled(self, stalled_after: timedelta, tasks: frozenset[str]) -> int:
        self.requeued_for.append(tasks)
        return 0


def worker(queue: FakeQueue, registry: TaskRegistry) -> Worker:
    return Worker(queue, registry, worker_id="worker-test", clock=lambda: NOW)


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
        "message": JOB_FAILURE_TEXT["PROVIDER_UNAVAILABLE"],
        "retryable": False,
    }


@pytest.mark.parametrize(
    ("retry_after_s", "waits"),
    [(120.0, timedelta(seconds=120)), (1.0, backoff(1)), (None, backoff(1))],
)
async def test_fr_028_a_provider_pause_longer_than_the_backoff_is_waited_out(
    retry_after_s: float | None, waits: timedelta
) -> None:
    registry = TaskRegistry()

    @registry.task("catalog.import_products")
    async def throttled(ctx: JobContext) -> None:
        raise RateLimitedError("slow down", provider="whatsapp", retry_after_s=retry_after_s)

    queue = FakeQueue([claimed()])
    await worker(queue, registry).run_once()

    ((_, error, retry_at),) = queue.events
    assert error["retryable"] is True
    assert retry_at == NOW + waits


async def test_a_job_that_crashes_keeps_the_raw_error_out_of_its_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = TaskRegistry()
    sql = "(psycopg.errors.UniqueViolation) [SQL: INSERT INTO sellers (id) VALUES (%(id)s)]"

    @registry.task("catalog.import_products")
    async def crash(ctx: JobContext) -> None:
        raise RuntimeError(sql)

    queue = FakeQueue([claimed(attempt=3)])
    with caplog.at_level(logging.WARNING, logger="vitrina.jobs"):
        await worker(queue, registry).run_once()

    assert queue.events == [
        (
            "fail",
            {
                "code": "INTERNAL_ERROR",
                "message": "Algo falló de nuestro lado. Intenta de nuevo.",
                "retryable": False,
            },
            None,
        )
    ]
    assert sql in caplog.text
    assert str(JOB) in caplog.text


async def test_a_refused_provider_call_stores_a_safe_message_and_logs_the_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = TaskRegistry()

    @registry.task("catalog.import_products")
    async def refused(ctx: JobContext) -> None:
        raise ProviderError("400 key sk-synthetic rejected", provider="meta_model_api")

    queue = FakeQueue([claimed()])
    with caplog.at_level(logging.WARNING, logger="vitrina.jobs"):
        await worker(queue, registry).run_once()

    assert queue.events == [
        (
            "fail",
            {
                "code": "PROVIDER_ERROR",
                "message": JOB_FAILURE_TEXT["PROVIDER_ERROR"],
                "retryable": False,
            },
            None,
        )
    ]
    assert "sk-synthetic" in caplog.text


async def test_a_task_that_words_its_failure_for_the_seller_keeps_its_text() -> None:
    registry = TaskRegistry()
    spanish = "No pudimos leer los productos de la foto. Inténtalo de nuevo."

    @registry.task("catalog.import_products")
    async def unreadable(ctx: JobContext) -> None:
        raise JobFailedError("PROVIDER_ERROR", spanish)

    queue = FakeQueue([claimed()])
    await worker(queue, registry).run_once()

    assert queue.events[0][1]["message"] == spanish


async def test_a_missing_setting_behind_a_failed_job_only_reaches_the_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The setting must be missing whatever the developer's `.env` or shell holds.
    monkeypatch.delenv("MODEL_AGENT", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    registry = TaskRegistry()

    @registry.task("catalog.import_products")
    async def unconfigured(ctx: JobContext) -> None:
        try:
            ctx.settings.model_id("agent")
        except LookupError as error:
            raise JobFailedError.safe("PROVIDER_UNAVAILABLE") from error

    queue = FakeQueue([claimed()])
    unconfigured_worker = Worker(
        queue, registry, settings=settings, worker_id="worker-test", clock=lambda: NOW
    )
    with caplog.at_level(logging.WARNING, logger="vitrina.jobs"):
        await unconfigured_worker.run_once()

    assert queue.events[0][1]["message"] == JOB_FAILURE_TEXT["PROVIDER_UNAVAILABLE"]
    assert "MODEL_AGENT" in caplog.text


def test_a_stored_internal_error_is_never_shown_as_written() -> None:
    row = {
        "id": JOB,
        "kind": "brief",
        "status": "failed",
        "result_ref": None,
        "created_at": NOW,
        "started_at": NOW,
        "finished_at": NOW,
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "ProgrammingError: [SQL: SELECT * FROM sellers]",
            "retryable": False,
        },
    }

    job = Job.from_row(row)

    assert job.error is not None
    assert job.error.code == "INTERNAL_ERROR"
    assert "SQL" not in job.model_dump_json()


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


def told_task(failure: BaseException | None = None, *, timeout_s: float = 600.0) -> TaskRegistry:
    """One task that raises ``failure`` (or hangs) and tells the queue's events when it gives up."""
    registry = TaskRegistry()

    async def handler(ctx: JobContext) -> None:
        if failure is None:
            await asyncio.sleep(30)
        else:
            raise failure

    async def told(ctx: JobContext, code: str) -> None:
        assert isinstance(ctx.queue, FakeQueue)
        ctx.queue.events.append(("told", ctx.job_id, code))

    registry.add("catalog.import_products", handler, timeout_s=timeout_s, on_final_failure=told)
    return registry


@pytest.mark.parametrize(
    ("failure", "attempt", "code"),
    [
        (
            JobFailedError("PRODUCT_NOT_FOUND", "No encontramos el producto."),
            1,
            "PRODUCT_NOT_FOUND",
        ),
        (ProviderError("400 from images", provider="meta_model_api"), 1, "PROVIDER_ERROR"),
        (
            ProviderError("503 from images", provider="meta_model_api", retryable=True),
            3,
            "PROVIDER_UNAVAILABLE",
        ),
        (RuntimeError("a bug"), 3, "INTERNAL_ERROR"),
    ],
)
async def test_fr_083_a_task_is_told_once_after_its_job_failed_for_good(
    failure: BaseException, attempt: int, code: str
) -> None:
    queue = FakeQueue([claimed(attempt=attempt)])

    await worker(queue, told_task(failure)).run_once()

    assert [event[0] for event in queue.events] == ["fail", "told"]  # the queue records it first
    assert queue.events[0][1]["code"] == code
    assert queue.events[0][2] is None
    assert queue.events[1] == ("told", JOB, code)


async def test_fr_083_a_task_is_told_when_its_last_attempt_times_out() -> None:
    queue = FakeQueue([claimed(attempt=3)])

    await worker(queue, told_task(timeout_s=0.01)).run_once()

    assert queue.events[-1] == ("told", JOB, "TIMEOUT")


@pytest.mark.parametrize(
    "failure",
    [
        ProviderError("503 from images", provider="meta_model_api", retryable=True),
        RuntimeError("a bug"),
        None,  # hangs until the timeout
    ],
)
async def test_fr_083_a_task_is_not_told_while_attempts_remain(
    failure: BaseException | None,
) -> None:
    queue = FakeQueue([claimed(attempt=1)])

    await worker(queue, told_task(failure, timeout_s=0.01)).run_once()

    assert [event[0] for event in queue.events] == ["fail"]
    assert queue.events[0][2] is not None  # it comes back


async def test_fr_083_a_failing_notice_leaves_the_job_failed_and_the_worker_running() -> None:
    registry = TaskRegistry()

    async def handler(ctx: JobContext) -> None:
        raise JobFailedError("PRODUCT_NOT_FOUND", "No encontramos el producto.")

    async def told(ctx: JobContext, code: str) -> None:
        raise RuntimeError("the database went away")

    registry.add("catalog.import_products", handler, on_final_failure=told)
    queue = FakeQueue([claimed()])

    assert await worker(queue, registry).run_once() is True

    assert [event[0] for event in queue.events] == ["fail"]


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


INBOUND = "conversation.process_inbound"
CAMPAIGN = "compliance.campaign_send"


async def _noop(ctx: JobContext) -> None:
    return None


async def _no_sweep(sessions: Any, now: datetime) -> int:
    return 0


def lanes_registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.add(INBOUND, _noop)
    registry.add(CAMPAIGN, _noop)
    registry.every("catalog.expire_reservations", every_s=600, sweep=_no_sweep)
    return registry


async def test_a_worker_excluding_inbound_never_claims_it_even_when_it_is_oldest() -> None:
    general = lanes_registry().narrowed(exclude={INBOUND})
    queue = FakeQueue([claimed(task=INBOUND), claimed(task=CAMPAIGN)])

    assert general.names() == {CAMPAIGN}
    assert await worker(queue, general).run_once() is True
    assert await worker(queue, general).run_once() is False
    assert [job.task for job in queue.pending] == [INBOUND]


async def test_a_worker_limited_to_inbound_claims_only_inbound() -> None:
    inbound = lanes_registry().narrowed(only={INBOUND})
    queue = FakeQueue([claimed(task=CAMPAIGN), claimed(task=INBOUND)])

    assert inbound.names() == {INBOUND}
    assert await worker(queue, inbound).run_once() is True
    assert await worker(queue, inbound).run_once() is False
    assert [job.task for job in queue.pending] == [CAMPAIGN]


async def test_a_worker_with_no_limit_claims_every_task() -> None:
    registry = lanes_registry().narrowed()
    queue = FakeQueue([claimed(task=CAMPAIGN), claimed(task=INBOUND)])

    assert registry.names() == {INBOUND, CAMPAIGN}
    assert await worker(queue, registry).run_once() is True
    assert await worker(queue, registry).run_once() is True
    assert queue.pending == []


def test_only_the_general_worker_keeps_the_sweeps() -> None:
    names = [item.name for item in lanes_registry().narrowed(exclude={INBOUND}).periodic()]

    assert names == ["catalog.expire_reservations"]
    assert lanes_registry().narrowed(only={INBOUND}).periodic() == ()
    assert len(lanes_registry().narrowed().periodic()) == 1


@pytest.mark.parametrize("limit", ["only", "exclude"])
def test_a_task_name_that_does_not_exist_is_refused(limit: str) -> None:
    with pytest.raises(ValueError, match=r"unknown task.*conversation\.process_inbund"):
        lanes_registry().narrowed(**{limit: {"conversation.process_inbund"}})


def test_only_and_exclude_cannot_be_combined() -> None:
    with pytest.raises(ValueError, match="either"):
        lanes_registry().narrowed(only={INBOUND}, exclude={CAMPAIGN})


def test_excluding_every_task_is_refused() -> None:
    with pytest.raises(ValueError, match="no task left"):
        lanes_registry().narrowed(exclude={INBOUND, CAMPAIGN})


@pytest.mark.parametrize(
    "limits", [{"only": {INBOUND}}, {"exclude": {INBOUND}}, {}], ids=["only", "exclude", "none"]
)
async def test_a_worker_requeues_only_the_stalled_jobs_of_the_tasks_it_claims(
    limits: dict[str, set[str]],
) -> None:
    stop = asyncio.Event()

    class OneLoop(FakeQueue):
        async def claim(self, worker_id: str, tasks: frozenset[str]) -> ClaimedJob | None:
            stop.set()
            return None

    registry = lanes_registry().narrowed(**limits)
    queue = OneLoop()

    await worker(queue, registry).run(stop)

    assert queue.requeued_for == [registry.names()]
