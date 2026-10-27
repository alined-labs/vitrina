"""Creative jobs on Postgres (T034): the job row, its creatives and its copy variants.

A creative job's id is the queue job's id, so a client can poll ``GET /v1/jobs/{id}`` or
``GET /v1/creative/jobs/{id}``. Rows are read for one seller only (Art. V.3); files stay in the
blob store and answers carry signed links. Blocked creatives are kept for audit (FR-013).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.http import keyset_page, next_cursor
from vitrina_core.jobs import JobErrorDetail
from vitrina_core.storage import BlobStore
from vitrina_creative.schemas import (
    CopyVariant,
    Creative,
    CreativeJob,
    CreativeJobCreate,
    ProvenanceView,
    VerificationView,
)
from vitrina_creative.tables import copy_variants, creative_jobs, creatives

Row = sa.RowMapping


class CreativeJobStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @staticmethod
    async def create(
        session: AsyncSession, *, job_id: UUID, seller_id: UUID, params: CreativeJobCreate
    ) -> None:
        """Insert the queued job inside the caller's transaction, next to the queue's job."""
        await session.execute(
            sa.insert(creative_jobs).values(
                id=job_id,
                seller_id=seller_id,
                product_id=params.product_id,
                params=params.model_dump(mode="json"),
                status="queued",
                stage="queued",
                progress=0,
                brief_id=params.brief_id,
            )
        )

    async def get(self, seller_id: UUID, job_id: UUID) -> Row | None:
        statement = sa.select(creative_jobs).where(
            creative_jobs.c.id == job_id, creative_jobs.c.seller_id == seller_id
        )
        async with self.sessions() as session:
            return (await session.execute(statement)).mappings().one_or_none()

    async def page(
        self, seller_id: UUID, *, limit: int, cursor: str | None, product_id: UUID | None = None
    ) -> tuple[list[Row], str | None]:
        statement = sa.select(creative_jobs).where(creative_jobs.c.seller_id == seller_id)
        if product_id is not None:
            statement = statement.where(creative_jobs.c.product_id == product_id)
        statement = keyset_page(statement, creative_jobs, limit=limit, cursor=cursor)
        async with self.sessions() as session:
            rows = list((await session.execute(statement)).mappings())
        return next_cursor(rows, limit=limit)

    async def set_stage(self, job_id: UUID, stage: str, progress: float) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(creative_jobs)
                .where(creative_jobs.c.id == job_id)
                .values(status="running", stage=stage, progress=round(progress, 3))
            )

    async def finish(
        self, job_id: UUID, scenes: list[dict[str, Any]], copy: list[dict[str, Any]]
    ) -> None:
        """Store the results and mark the job ready in one transaction.

        A retry of an unfinished job replaces its rows; once the job succeeded its rows stay,
        because ads point at them (``ads`` cascades on creatives).

        Rows of one transaction share ``now()`` and UUIDv7 ids are random within a millisecond,
        so each row gets its own ``created_at``, a microsecond apart in generation order, and
        reads ordered by ``(created_at, id)`` return scenes and copy as they were made.
        """
        finished = datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            status = await session.scalar(
                sa.select(creative_jobs.c.status)
                .where(creative_jobs.c.id == job_id)
                .with_for_update()
            )
            if status == "succeeded":  # a retry after success: ads may reference these rows
                return
            await session.execute(sa.delete(copy_variants).where(copy_variants.c.job_id == job_id))
            await session.execute(sa.delete(creatives).where(creatives.c.job_id == job_id))
            if scenes:
                await session.execute(sa.insert(creatives), _in_order(scenes, finished))
            if copy:
                await session.execute(sa.insert(copy_variants), _in_order(copy, finished))
            await session.execute(
                sa.update(creative_jobs)
                .where(creative_jobs.c.id == job_id)
                .values(
                    status="succeeded",
                    stage="ready",
                    progress=1,
                    error=None,
                    finished_at=finished,
                )
            )

    async def fail(self, job_id: UUID, code: str, message: str) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(creative_jobs)
                .where(creative_jobs.c.id == job_id)
                .values(
                    status="failed",
                    stage="failed",
                    error={"code": code, "message": message, "retryable": False},
                    finished_at=datetime.now(UTC),
                )
            )

    async def creatives_for(self, seller_id: UUID, job_id: UUID) -> list[Row]:
        statement = (
            sa.select(creatives)
            .where(creatives.c.job_id == job_id, creatives.c.seller_id == seller_id)
            .order_by(creatives.c.created_at, creatives.c.id)
        )
        async with self.sessions() as session:
            return list((await session.execute(statement)).mappings())

    async def copy_for(self, seller_id: UUID, job_id: UUID) -> list[Row]:
        statement = (
            sa.select(copy_variants)
            .where(copy_variants.c.job_id == job_id, copy_variants.c.seller_id == seller_id)
            .order_by(copy_variants.c.created_at, copy_variants.c.id)
        )
        async with self.sessions() as session:
            return list((await session.execute(statement)).mappings())

    async def creative(self, seller_id: UUID, creative_id: UUID) -> Row | None:
        statement = sa.select(creatives).where(
            creatives.c.id == creative_id, creatives.c.seller_id == seller_id
        )
        async with self.sessions() as session:
            return (await session.execute(statement)).mappings().one_or_none()


async def creative_view(row: Row, blobs: BlobStore) -> Creative:
    return Creative(
        id=row["id"],
        job_id=row["job_id"],
        product_id=row["product_id"],
        format=row["format"],
        image_url=await blobs.signed_url(row["storage_key"]),
        mask_url=await blobs.signed_url(row["mask_key"]) if row["mask_key"] else MISSING,
        source_asset_id=row["source_asset_id"] if row["source_asset_id"] else MISSING,
        scene_prompt=row["scene_prompt"] or MISSING,
        verification=VerificationView.model_validate(row["verification"]),
        provenance=ProvenanceView.model_validate(row["provenance"])
        if row["provenance"]
        else MISSING,
        created_at=row["created_at"],
    )


def copy_view(row: Row) -> CopyVariant:
    score = row["brand_voice_score"]
    return CopyVariant(
        id=row["id"],
        job_id=row["job_id"],
        hook=row["hook"],
        angle=row["angle"],
        text=row["text"],
        brand_voice_score=float(score) if score is not None else None,
        brand_voice_judge=row["brand_voice_judge"],
        status=row["status"],
    )


async def job_view(
    row: Row,
    *,
    creative_rows: list[Row] | None = None,
    copy_rows: list[Row] | None = None,
    blobs: BlobStore | None = None,
) -> CreativeJob:
    scenes: list[Creative] | None = None
    if creative_rows is not None and blobs is not None:
        scenes = [await creative_view(scene, blobs) for scene in creative_rows]
    return CreativeJob(
        id=row["id"],
        seller_id=row["seller_id"],
        product_id=row["product_id"],
        params=CreativeJobCreate.model_validate(row["params"]) if row["params"] else MISSING,
        status=row["status"],
        stage=row["stage"],
        progress=float(row["progress"]),
        creatives=scenes if scenes is not None else MISSING,
        copy_variants=[copy_view(c) for c in copy_rows] if copy_rows is not None else MISSING,
        error=JobErrorDetail.model_validate(row["error"]) if row["error"] else None,
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _in_order(rows: list[dict[str, Any]], start: datetime) -> list[dict[str, Any]]:
    return [
        {**row, "created_at": start + timedelta(microseconds=index)}
        for index, row in enumerate(rows)
    ]
