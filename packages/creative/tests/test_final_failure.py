"""A job that ended for good leaves no spinner and no anonymous photo (FR-072, FR-083).

What the worker tells the tasks, without a database; the same paths run on Postgres in
``apps/api/tests/test_creative_db.py`` and ``test_public_db.py``.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from vitrina_core.jobs import ClaimedJob, JobContext, TaskRegistry
from vitrina_core.settings import Settings
from vitrina_core.storage import BlobNotFoundError, LocalBlobStore, blob_key
from vitrina_creative.jobs import JOB_GAVE_UP, CreativeJobStore
from vitrina_creative.reconcile import reconcile_failed_jobs
from vitrina_creative.showcase import OWNER, SHOWCASE_TASK, ShowcaseStore, showcase_gave_up
from vitrina_creative.tasks import CREATIVE_TASK, creative_gave_up, register

JOB = UUID("00000000-0000-7000-8000-000000000001")
SHOWCASE = UUID("00000000-0000-7000-8000-000000000002")
SOURCE = blob_key(OWNER, "showcases", "bolso-3f9a1c/source", "png")
SESSIONS: Any = object()


def context(
    task: str, payload: dict[str, Any], *, storage: Any = None, sessions: Any = SESSIONS
) -> JobContext:
    job = ClaimedJob(
        id=JOB,
        kind="public_showcase",
        task=task,
        payload=payload,
        owner_seller_id=None,
        attempt=1,
        max_attempts=3,
    )
    queue: Any = object()
    return JobContext(
        job,
        queue,
        sessions,
        None,
        storage,
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def failed_showcases(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    failed: list[UUID] = []

    async def fail(_store: ShowcaseStore, showcase_id: UUID) -> None:
        failed.append(showcase_id)

    monkeypatch.setattr(ShowcaseStore, "fail", fail)
    return failed


def showcase_payload() -> dict[str, str]:
    return {"showcase_id": str(SHOWCASE), "source_key": SOURCE}


async def test_as_8_2_a_showcase_that_gave_up_fails_its_page_and_deletes_the_upload(
    tmp_path: Path, failed_showcases: list[UUID]
) -> None:
    storage = LocalBlobStore(tmp_path, public_url="http://localhost:8000")
    await storage.put(SOURCE, b"photo", "image/png")

    await showcase_gave_up(context(SHOWCASE_TASK, showcase_payload(), storage=storage), "TIMEOUT")

    assert failed_showcases == [SHOWCASE]
    with pytest.raises(BlobNotFoundError):
        await storage.get(SOURCE)


async def test_as_8_2_an_upload_already_gone_still_fails_the_page(
    tmp_path: Path, failed_showcases: list[UUID]
) -> None:
    storage = LocalBlobStore(tmp_path, public_url="http://localhost:8000")

    await showcase_gave_up(context(SHOWCASE_TASK, showcase_payload(), storage=storage), "STALLED")

    assert failed_showcases == [SHOWCASE]


async def test_as_8_2_a_page_stays_open_for_the_sweep_when_the_upload_cannot_be_deleted(
    failed_showcases: list[UUID],
) -> None:
    class BrokenStorage:
        async def delete(self, key: str) -> None:
            raise OSError("storage is down")

    with pytest.raises(OSError, match="storage is down"):
        await showcase_gave_up(
            context(SHOWCASE_TASK, showcase_payload(), storage=BrokenStorage()), "TIMEOUT"
        )

    assert failed_showcases == []


@pytest.mark.parametrize(
    ("queue_code", "row_code"),
    [
        ("TIMEOUT", "TIMEOUT"),
        ("STALLED", "STALLED"),
        ("PROVIDER_ERROR", "PROVIDER_ERROR"),
        # The dashboard reads PROVIDER_UNAVAILABLE as "the service is not configured"; a provider
        # that kept failing is not that.
        ("PROVIDER_UNAVAILABLE", "PROVIDER_ERROR"),
        ("INTERNAL_ERROR", "INTERNAL_ERROR"),
    ],
)
async def test_as_2_1_a_creative_job_that_gave_up_tells_the_seller_in_spanish(
    monkeypatch: pytest.MonkeyPatch, queue_code: str, row_code: str
) -> None:
    failed: list[tuple[UUID, str, str]] = []

    async def fail(_store: CreativeJobStore, job_id: UUID, code: str, message: str) -> None:
        failed.append((job_id, code, message))

    monkeypatch.setattr(CreativeJobStore, "fail", fail)

    await creative_gave_up(context(CREATIVE_TASK, {}), queue_code)

    assert failed == [(JOB, row_code, JOB_GAVE_UP)]
    assert "No pudimos" in JOB_GAVE_UP


def test_as_2_1_both_creative_tasks_are_told_and_the_sweep_is_registered() -> None:
    registry = TaskRegistry()
    register(registry)

    creative, showcase = registry.get(CREATIVE_TASK), registry.get(SHOWCASE_TASK)
    assert creative is not None
    assert showcase is not None
    assert creative.on_final_failure is creative_gave_up
    assert showcase.on_final_failure is showcase_gave_up
    assert "creative.reconcile_failed_jobs" in {item.name for item in registry.periodic()}


def sessions_answering(*answers: list[tuple[Any, ...]]) -> Any:
    """A session factory whose queries answer in turn: failed creative jobs, failed showcases."""
    pending = list(answers)

    class Session:
        async def execute(self, _statement: Any) -> Any:
            rows = pending.pop(0)
            return SimpleNamespace(all=lambda: rows)

    @asynccontextmanager
    async def sessions() -> Any:
        yield Session()

    return sessions


async def test_as_8_2_the_sweep_goes_on_after_an_upload_it_cannot_delete_and_retries_it_later(
    monkeypatch: pytest.MonkeyPatch, failed_showcases: list[UUID]
) -> None:
    broken_key = blob_key(OWNER, "showcases", "broken-3f9a1c/source", "png")
    broken = UUID("00000000-0000-7000-8000-000000000003")
    deleted: list[str] = []
    closed: list[bool] = []

    class Storage:
        async def delete(self, key: str) -> None:
            if key == broken_key:
                raise OSError("storage is down")
            deleted.append(key)

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr("vitrina_creative.reconcile.blob_store", lambda _settings: Storage())
    sessions = sessions_answering(
        [], [(broken, {"source_key": broken_key}), (SHOWCASE, {"source_key": SOURCE})]
    )

    changed = await reconcile_failed_jobs(sessions, datetime.now(UTC))

    assert changed == 1
    assert deleted == [SOURCE]
    assert failed_showcases == [SHOWCASE]  # the other row stays open for the next sweep
    assert closed == [True]


async def test_as_2_1_the_sweep_reads_the_queues_code_and_needs_no_storage_for_creative_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed: list[tuple[UUID, str]] = []

    async def give_up(_store: CreativeJobStore, job_id: UUID, code: str) -> None:
        failed.append((job_id, code))

    def no_storage(_settings: Any) -> Any:
        raise AssertionError("no upload to delete")

    monkeypatch.setattr(CreativeJobStore, "give_up", give_up)
    monkeypatch.setattr("vitrina_creative.reconcile.blob_store", no_storage)
    sessions = sessions_answering([(JOB, {"code": "STALLED"}), (SHOWCASE, None)], [])

    assert await reconcile_failed_jobs(sessions, datetime.now(UTC)) == 2

    assert failed == [(JOB, "STALLED"), (SHOWCASE, "INTERNAL_ERROR")]
