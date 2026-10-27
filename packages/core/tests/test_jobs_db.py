import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import jobs, sellers
from vitrina_core.jobs import PostgresJobQueue

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")
TASKS = frozenset({"catalog.import_products"})


async def add_sellers(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        for seller_id, number in ((SELLER, "+573000000001"), (OTHER, "+573000000002")):
            await session.execute(
                sa.insert(sellers).values(
                    id=seller_id,
                    business_name="Tienda",
                    owner_name="Ana",
                    whatsapp_number=number,
                    city="Cali",
                    consent_prompt_text="¿Te aviso? Responde SÍ",
                )
            )


async def enqueue(
    queue: PostgresJobQueue, sessions: async_sessionmaker[AsyncSession], **extra: object
) -> UUID:
    async with sessions() as session, session.begin():
        return await queue.enqueue(
            session,
            kind="product_import",
            task="catalog.import_products",
            owner_seller_id=SELLER,
            **extra,
        )


@pytest.mark.db
async def test_db_fr_083_enqueue_is_part_of_the_callers_transaction(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_sellers(db_sessions)
    queue = PostgresJobQueue(db_sessions)

    async def request_that_fails_after_enqueueing() -> None:
        async with db_sessions() as session, session.begin():
            await queue.enqueue(
                session,
                kind="product_import",
                task="catalog.import_products",
                owner_seller_id=SELLER,
            )
            raise RuntimeError("the request failed after enqueueing")

    with pytest.raises(RuntimeError):
        await request_that_fails_after_enqueueing()

    assert await queue.claim("w1", TASKS) is None


@pytest.mark.db
async def test_db_fr_083_a_job_is_claimed_once_even_by_concurrent_workers(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_sellers(db_sessions)
    queue = PostgresJobQueue(db_sessions)
    job_id = await enqueue(queue, db_sessions, payload={"image_key": "menu.jpg"})

    first, second = await asyncio.gather(queue.claim("w1", TASKS), queue.claim("w2", TASKS))

    winners = [job for job in (first, second) if job is not None]
    assert [job.id for job in winners] == [job_id]
    assert winners[0].payload == {"image_key": "menu.jpg"}
    assert winners[0].attempt == 1
    seller_view = await queue.get_for_seller(SELLER, job_id)
    assert seller_view is not None
    assert seller_view.status == "running"
    assert await queue.get_for_seller(OTHER, job_id) is None


@pytest.mark.db
async def test_db_failed_jobs_wait_for_their_retry_then_fail_for_good(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_sellers(db_sessions)
    queue = PostgresJobQueue(db_sessions)
    job_id = await enqueue(queue, db_sessions, max_attempts=2)

    claimed = await queue.claim("w1", TASKS)
    assert claimed is not None
    async with db_sessions() as session:
        retry_at = await session.scalar(sa.select(sa.func.now() + timedelta(minutes=5)))
    await queue.fail(
        job_id, {"code": "PROVIDER_UNAVAILABLE", "message": "503", "retryable": True}, retry_at
    )
    assert await queue.claim("w1", TASKS) is None

    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.update(jobs).where(jobs.c.id == job_id).values(run_after=sa.func.now())
        )
    again = await queue.claim("w1", TASKS)
    assert again is not None
    assert again.attempt == 2
    await queue.fail(
        job_id, {"code": "PROVIDER_UNAVAILABLE", "message": "503", "retryable": False}, None
    )

    view = await queue.get_for_seller(SELLER, job_id)
    assert view is not None
    assert (view.status, view.error is not None, view.finished_at is not None) == (
        "failed",
        True,
        True,
    )


@pytest.mark.db
async def test_db_succeeded_jobs_keep_their_result_and_finish_at_full_progress(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_sellers(db_sessions)
    queue = PostgresJobQueue(db_sessions)
    job_id = await enqueue(queue, db_sessions)
    await queue.claim("w1", TASKS)

    await queue.progress(job_id, "extracting", 0.5)
    await queue.succeed(job_id, "/v1/products?status=draft")

    view = await queue.get_for_seller(SELLER, job_id)
    assert view is not None
    assert (view.status, view.result_ref, view.progress) == (
        "succeeded",
        "/v1/products?status=draft",
        1.0,
    )


@pytest.mark.db
async def test_db_stalled_jobs_go_back_to_the_queue(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_sellers(db_sessions)
    queue = PostgresJobQueue(db_sessions)
    job_id = await enqueue(queue, db_sessions)
    await queue.claim("w1", TASKS)
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(heartbeat_at=sa.func.now() - timedelta(minutes=10))
        )

    assert await queue.requeue_stalled(timedelta(minutes=1)) == 1
    reclaimed = await queue.claim("w2", TASKS)
    assert reclaimed is not None
    assert reclaimed.attempt == 2


@pytest.mark.db
async def test_db_the_same_idempotency_key_enqueues_once(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_sellers(db_sessions)
    queue = PostgresJobQueue(db_sessions)

    first = await enqueue(queue, db_sessions, idempotency_key="sweep:2026-10-27T15")
    second = await enqueue(queue, db_sessions, idempotency_key="sweep:2026-10-27T15")

    assert first == second
