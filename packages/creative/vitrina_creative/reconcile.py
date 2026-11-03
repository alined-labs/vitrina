"""Rows the queue has given up on (FR-083), for the failures no code path sees.

The worker closes a job's row when it gives up on it. A worker that is killed (out of memory, a
deploy) does nothing: another one later fails the job in the queue (``requeue_stalled``), and the
row would stay open and the visitor's photo stored. This sweep reads the queue and closes those
rows. It only looks at jobs the queue says have failed, so a job queued for its next attempt or
still running is never touched, and rows already finished are not selected.
"""

import logging
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import jobs
from vitrina_core.settings import get_settings
from vitrina_core.storage import blob_store
from vitrina_creative.jobs import UNFINISHED, CreativeJobStore
from vitrina_creative.showcase import SHOWCASE_TASK, ShowcaseStore, close_failed_showcase
from vitrina_creative.tables import creative_jobs, public_showcases

logger = logging.getLogger("vitrina.creative")

RECONCILE_FAILED_JOBS = "creative.reconcile_failed_jobs"
RECONCILE_EVERY_S = 60.0

# A creative job's id is the queue job's id.
_FAILED_CREATIVE_JOBS = (
    sa.select(creative_jobs.c.id, jobs.c.error)
    .join_from(creative_jobs, jobs, creative_jobs.c.id == jobs.c.id)
    .where(creative_jobs.c.status.in_(UNFINISHED), jobs.c.status == "failed")
)
# A showcase's job names it in the payload, next to the upload's key.
_FAILED_SHOWCASES = (
    sa.select(public_showcases.c.id, jobs.c.payload)
    .join_from(
        public_showcases,
        jobs,
        jobs.c.payload["showcase_id"].astext == sa.cast(public_showcases.c.id, sa.Text),
    )
    .where(
        public_showcases.c.status == "processing",
        jobs.c.task == SHOWCASE_TASK,
        jobs.c.status == "failed",
    )
)


async def reconcile_failed_jobs(sessions: async_sessionmaker[AsyncSession], _now: datetime) -> int:
    """Close each open row whose job failed for good; returns how many."""
    async with sessions() as session:
        creative = (await session.execute(_FAILED_CREATIVE_JOBS)).all()
        showcases = (await session.execute(_FAILED_SHOWCASES)).all()
    store = CreativeJobStore(sessions)
    for job_id, error in creative:
        await store.give_up(job_id, str((error or {}).get("code") or "INTERNAL_ERROR"))
    if not showcases:
        return len(creative)
    # A sweep gets no storage from the worker; this one is rarely needed, and only for this pass.
    closed = 0
    storage = blob_store(get_settings())
    try:
        for showcase_id, payload in showcases:
            try:
                await close_failed_showcase(
                    ShowcaseStore(sessions), storage, showcase_id, str(payload["source_key"])
                )
            except Exception:
                logger.exception("showcase %s stays open for the next sweep", showcase_id)
            else:
                closed += 1
    finally:
        await storage.aclose()
    return len(creative) + closed
