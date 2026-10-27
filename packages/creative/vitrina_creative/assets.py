"""Assets (T030, FR-010): a seller's photos and short videos, bound to a product.

Files go to the blob store (I-15) under ``<seller_id>/assets/<asset_id>.<ext>``; the row keeps the
key, never a URL, and the API signs a short-lived link when it answers. The same file uploaded
twice by one seller is one asset (``sha256`` is unique per seller). Every read is scoped to the
seller (Art. V.3).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db import SellerScopedRepository
from vitrina_core.ids import new_id
from vitrina_core.storage import BlobStore, blob_key
from vitrina_creative.media import inspect_media
from vitrina_creative.tables import assets


class Asset(BaseModel):
    """openapi.yaml ``Asset``."""

    id: UUID
    seller_id: UUID
    product_id: UUID
    kind: Literal["image", "video"]
    url: Annotated[str, Field(json_schema_extra={"format": "uri"})]
    width: int | MISSING = MISSING
    height: int | MISSING = MISSING
    duration_s: float | None = None
    sha256: str
    created_at: datetime


@dataclass(frozen=True)
class AssetRecord:
    id: UUID
    seller_id: UUID
    product_id: UUID
    kind: Literal["image", "video"]
    storage_key: str
    sha256: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AssetStore(Protocol):
    async def add(self, record: AssetRecord) -> AssetRecord:
        """Store the row; when the seller already has this file, return that row instead."""
        ...

    async def get(self, seller_id: UUID, asset_id: UUID) -> AssetRecord | None: ...

    async def by_sha256(self, seller_id: UUID, sha256: str) -> AssetRecord | None: ...

    async def for_product(self, seller_id: UUID, product_id: UUID) -> list[AssetRecord]: ...


class InMemoryAssetStore:
    """The store without a database (tests, replay, laptops), with the same rules."""

    def __init__(self) -> None:
        self._rows: dict[UUID, AssetRecord] = {}

    async def add(self, record: AssetRecord) -> AssetRecord:
        existing = await self.by_sha256(record.seller_id, record.sha256)
        if existing is not None:
            return existing
        self._rows[record.id] = record
        return record

    async def get(self, seller_id: UUID, asset_id: UUID) -> AssetRecord | None:
        row = self._rows.get(asset_id)
        return row if row is not None and row.seller_id == seller_id else None

    async def by_sha256(self, seller_id: UUID, sha256: str) -> AssetRecord | None:
        return next(
            (r for r in self._rows.values() if r.seller_id == seller_id and r.sha256 == sha256),
            None,
        )

    async def for_product(self, seller_id: UUID, product_id: UUID) -> list[AssetRecord]:
        rows = [
            r
            for r in self._rows.values()
            if r.seller_id == seller_id and r.product_id == product_id
        ]
        return sorted(rows, key=lambda r: (r.created_at, r.id))


class _Assets(SellerScopedRepository):
    table = assets


class PostgresAssetStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, record: AssetRecord) -> AssetRecord:
        values = {
            "id": record.id,
            "product_id": record.product_id,
            "kind": record.kind,
            "storage_key": record.storage_key,
            "width": record.width,
            "height": record.height,
            "duration_s": record.duration_s,
            "sha256": record.sha256,
        }
        try:
            async with self._sessions() as session, session.begin():
                row = await _Assets(session, record.seller_id).insert(values)
                return _record(row)
        except IntegrityError:  # the same file arrived twice at once: the first row wins
            existing = await self.by_sha256(record.seller_id, record.sha256)
            if existing is None:
                raise
            return existing

    async def get(self, seller_id: UUID, asset_id: UUID) -> AssetRecord | None:
        async with self._sessions() as session:
            row = await _Assets(session, seller_id).get(asset_id)
            return _record(row) if row is not None else None

    async def by_sha256(self, seller_id: UUID, sha256: str) -> AssetRecord | None:
        async with self._sessions() as session:
            repo = _Assets(session, seller_id)
            result = await session.execute(repo.select().where(assets.c.sha256 == sha256))
            row = result.mappings().one_or_none()
            return _record(row) if row is not None else None

    async def for_product(self, seller_id: UUID, product_id: UUID) -> list[AssetRecord]:
        async with self._sessions() as session:
            repo = _Assets(session, seller_id)
            statement = (
                repo.select()
                .where(assets.c.product_id == product_id)
                .order_by(assets.c.created_at, assets.c.id)
            )
            result = await session.execute(statement)
            return [_record(row) for row in result.mappings()]


def assets_from_state(state: Any) -> AssetStore:
    """The process's asset store: Postgres when the app has a database, in memory otherwise."""
    store: AssetStore | None = getattr(state, "assets", None)
    if store is None:
        sessions = getattr(state, "sessions", None)
        store = PostgresAssetStore(sessions) if sessions is not None else InMemoryAssetStore()
        state.assets = store
    return store


async def upload_asset(
    *, seller_id: UUID, product_id: UUID, data: bytes, store: AssetStore, blobs: BlobStore
) -> AssetRecord:
    """Inspect, store the file, record the asset; the same file twice is one asset (FR-010)."""
    info = inspect_media(data)  # MediaRejectedError for anything outside FR-010
    digest = hashlib.sha256(data).hexdigest()
    existing = await store.by_sha256(seller_id, digest)
    if existing is not None:
        return existing
    asset_id = new_id()
    key = blob_key(seller_id, "assets", asset_id, info.extension)
    await blobs.put(key, data, info.mime_type)
    record = await store.add(
        AssetRecord(
            id=asset_id,
            seller_id=seller_id,
            product_id=product_id,
            kind=info.kind,
            storage_key=key,
            sha256=digest,
            width=info.width,
            height=info.height,
            duration_s=info.duration_s,
        )
    )
    if record.id != asset_id:  # lost a race to an identical upload: drop our copy
        await blobs.delete(key)
    return record


async def asset_view(record: AssetRecord, blobs: BlobStore) -> Asset:
    return Asset(
        id=record.id,
        seller_id=record.seller_id,
        product_id=record.product_id,
        kind=record.kind,
        url=await blobs.signed_url(record.storage_key),
        width=record.width if record.width is not None else MISSING,
        height=record.height if record.height is not None else MISSING,
        duration_s=record.duration_s,
        sha256=record.sha256,
        created_at=record.created_at,
    )


async def list_asset_ids(store: AssetStore, seller_id: UUID, product_id: UUID) -> list[UUID]:
    """For ``Product.asset_ids`` (catalog's request): the product's assets, oldest first."""
    return [record.id for record in await store.for_product(seller_id, product_id)]


def _record(row: sa.RowMapping) -> AssetRecord:
    return AssetRecord(
        id=row["id"],
        seller_id=row["seller_id"],
        product_id=row["product_id"],
        kind=row["kind"],
        storage_key=row["storage_key"],
        sha256=row["sha256"],
        width=row["width"],
        height=row["height"],
        duration_s=row["duration_s"],
        created_at=row["created_at"],
    )
