"""Ads: a passed creative and a copy variant, approved with a short code (T042; FR-013..015).

An ad starts as a draft from a creative that passed verification and a copy variant of the
same job. Approving it assigns the seller's next code (``A`` and base 32: A1 … AZZZ) and the
``wa.me`` link that prefills "Hola, vi el anuncio #A7"; the database does both and refuses an
approved ad whose creative did not pass (invariant 2). Archiving keeps the code, so chats that
quote it still attribute. A blocked creative never becomes an ad (409 CREATIVE_BLOCKED), so it
never reaches a list, an export or a chat (FR-013). ``get_approved_asset`` is what the
conversation engine sends for ``send_ad_asset`` (I-10, T043).
"""

import io
import json
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import SellerStore
from vitrina_core.http import FieldError, ProblemCode, ProblemError, keyset_page, next_cursor
from vitrina_core.ids import new_id
from vitrina_core.providers import Blob
from vitrina_core.storage import BlobStore
from vitrina_creative.copy import WA_LINK, CopyAngle
from vitrina_creative.provenance import AI_LABEL, provenance_of, provenance_record, publishable
from vitrina_creative.schemas import Uri
from vitrina_creative.tables import ads, copy_variants, creative_jobs, creatives

AdStatus = Literal["draft", "approved", "archived"]
CODE_PATTERN = r"^A[0-9A-Z]{1,3}$"
PASSED = creatives.c.verification["status"].astext == "passed"
Row = Mapping[Any, Any]


class AdCreate(BaseModel):
    creative_id: UUID
    copy_variant_id: UUID


class AdStats(BaseModel):
    chats: int | MISSING = MISSING
    reservations: int | MISSING = MISSING
    bookings: int | MISSING = MISSING


class Ad(BaseModel):
    id: UUID
    seller_id: UUID
    product_id: UUID
    creative_id: UUID
    copy_variant_id: UUID
    brief_id: UUID | None = None
    code: Annotated[str, Field(pattern=CODE_PATTERN)] | None = None
    wa_link: Uri | None = None
    angle: CopyAngle | MISSING = MISSING
    status: AdStatus
    stats: AdStats | MISSING = MISSING
    created_at: datetime
    approved_at: datetime | None = None


@dataclass(frozen=True)
class ApprovedAsset:
    """An approved ad ready for a chat: the image as published and its caption with the link."""

    ad_id: UUID
    code: str
    image: Blob
    caption: str
    wa_link: str


def fill_link(text: str, wa_link: str | None) -> str:
    """The copy as published: the ``{wa_link}`` marker becomes the ad's link (or goes)."""
    return text.replace(WA_LINK, wa_link or "").strip()


def ad_view(row: Row) -> Ad:
    optional: dict[str, Any] = {}
    if row["angle"] is not None:
        optional["angle"] = row["angle"]
    return Ad(
        id=row["id"],
        seller_id=row["seller_id"],
        product_id=row["product_id"],
        creative_id=row["creative_id"],
        copy_variant_id=row["copy_variant_id"],
        brief_id=row["brief_id"],
        code=row["code"],
        wa_link=row["wa_link"],
        status=row["status"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        **optional,
    )


def _not_found(what: str, item: UUID | str) -> ProblemError:
    return ProblemError(ProblemCode.NOT_FOUND, detail=f"no {what} {item} for this seller")


def _blocked() -> ProblemError:
    return ProblemError(
        ProblemCode.CREATIVE_BLOCKED,
        detail="Esta imagen no pasó la verificación: no cambió tu producto, así que no se publica.",
    )


def _invalid_copy(message: str) -> ProblemError:
    return ProblemError(
        ProblemCode.VALIDATION_ERROR, errors=[FieldError(field="copy_variant_id", message=message)]
    )


class AdStore:
    """Ads on Postgres, scoped to the seller in every read and write (Art. V.3)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    def _select(self, seller_id: UUID) -> sa.Select[Any]:
        # Only creatives that passed: a blocked one never shows up anywhere (FR-013).
        return (
            sa.select(ads)
            .join(creatives, creatives.c.id == ads.c.creative_id)
            .where(ads.c.seller_id == seller_id, PASSED)
        )

    async def _one(self, statement: sa.Select[Any]) -> Ad | None:
        async with self.sessions() as session:
            row = (await session.execute(statement)).mappings().first()
        return ad_view(row) if row is not None else None

    async def get(self, seller_id: UUID, ad_id: UUID) -> Ad | None:
        return await self._one(self._select(seller_id).where(ads.c.id == ad_id))

    async def by_code(self, seller_id: UUID, code: str) -> Ad | None:
        """Any status: an archived ad's code still attributes the chats that quote it."""
        return await self._one(self._select(seller_id).where(ads.c.code == code.upper()))

    async def page(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        status: AdStatus | None = None,
        product_id: UUID | None = None,
    ) -> tuple[list[Ad], str | None]:
        statement = self._select(seller_id)
        if status is not None:
            statement = statement.where(ads.c.status == status)
        if product_id is not None:
            statement = statement.where(ads.c.product_id == product_id)
        async with self.sessions() as session:
            rows = (
                (await session.execute(keyset_page(statement, ads, limit=limit, cursor=cursor)))
                .mappings()
                .all()
            )
        page, token = next_cursor(rows, limit=limit)
        return [ad_view(row) for row in page], token

    async def create(self, seller_id: UUID, data: AdCreate) -> Ad:
        """A draft from a passed creative and a usable copy variant of the same job."""
        ad_id = new_id()
        async with self.sessions() as session, session.begin():
            creative = (
                (
                    await session.execute(
                        sa.select(
                            creatives.c.job_id, creatives.c.product_id, creatives.c.verification
                        ).where(
                            creatives.c.id == data.creative_id, creatives.c.seller_id == seller_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            if creative is None:
                raise _not_found("creative", data.creative_id)
            if (creative["verification"] or {}).get("status") != "passed":
                raise _blocked()
            variant = (
                (
                    await session.execute(
                        sa.select(
                            copy_variants.c.job_id, copy_variants.c.angle, copy_variants.c.status
                        ).where(
                            copy_variants.c.id == data.copy_variant_id,
                            copy_variants.c.seller_id == seller_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if variant is None:
                raise _not_found("copy variant", data.copy_variant_id)
            if variant["job_id"] != creative["job_id"]:
                raise _invalid_copy("ese texto es de otro trabajo creativo")
            if variant["status"] in ("dropped_low_voice", "rejected"):
                raise _invalid_copy("ese texto fue descartado")
            brief_id = await session.scalar(
                sa.select(creative_jobs.c.brief_id).where(creative_jobs.c.id == creative["job_id"])
            )
            await session.execute(
                sa.insert(ads).values(
                    id=ad_id,
                    seller_id=seller_id,
                    product_id=creative["product_id"],
                    creative_id=data.creative_id,
                    copy_variant_id=data.copy_variant_id,
                    brief_id=brief_id,
                    angle=variant["angle"],
                    status="draft",
                )
            )
        ad = await self.get(seller_id, ad_id)
        assert ad is not None
        return ad

    async def approve(self, seller_id: UUID, ad_id: UUID) -> Ad:
        """AS-2.5: the code and the link come from the database; approving twice is a no-op."""
        async with self.sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        sa.select(ads.c.status, ads.c.creative_id, ads.c.copy_variant_id)
                        .where(ads.c.id == ad_id, ads.c.seller_id == seller_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise _not_found("ad", ad_id)
            if row["status"] == "archived":
                raise ProblemError(
                    ProblemCode.CONFLICT, detail="Este anuncio está archivado; crea uno nuevo."
                )
            if row["status"] == "draft":
                verification = await session.scalar(
                    sa.select(creatives.c.verification).where(creatives.c.id == row["creative_id"])
                )
                if (verification or {}).get("status") != "passed":
                    raise _blocked()
                await session.execute(
                    sa.update(ads).where(ads.c.id == ad_id).values(status="approved")
                )
                await session.execute(
                    sa.update(copy_variants)
                    .where(copy_variants.c.id == row["copy_variant_id"])
                    .values(status="approved")
                )
        ad = await self.get(seller_id, ad_id)
        assert ad is not None
        return ad

    async def archive(self, seller_id: UUID, ad_id: UUID) -> bool:
        async with self.sessions() as session, session.begin():
            archived = await session.scalar(
                sa.update(ads)
                .where(ads.c.id == ad_id, ads.c.seller_id == seller_id)
                .values(status="archived")
                .returning(ads.c.id)
            )
        return archived is not None

    async def parts(self, seller_id: UUID, ad_id: UUID) -> tuple[Ad, Row] | None:
        """The ad and a row with its creative's and copy's fields, for an export."""
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(
                            ads,
                            creatives.c.format,
                            creatives.c.storage_key,
                            creatives.c.verification,
                            creatives.c.provenance.label("creative_provenance"),
                            creatives.c.scene_prompt,
                            copy_variants.c.text.label("copy_text"),
                        )
                        .join(creatives, creatives.c.id == ads.c.creative_id)
                        .join(copy_variants, copy_variants.c.id == ads.c.copy_variant_id)
                        .where(ads.c.id == ad_id, ads.c.seller_id == seller_id, PASSED)
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return ad_view(row), row

    async def approved_for(self, seller_id: UUID, product_id: UUID) -> list[Row]:
        """Approved ads of a product with what a chat needs, newest first."""
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(
                            ads.c.id,
                            ads.c.code,
                            ads.c.wa_link,
                            creatives.c.storage_key,
                            creatives.c.scene_prompt,
                            copy_variants.c.text.label("copy_text"),
                        )
                        .join(creatives, creatives.c.id == ads.c.creative_id)
                        .join(copy_variants, copy_variants.c.id == ads.c.copy_variant_id)
                        .where(
                            ads.c.seller_id == seller_id,
                            ads.c.product_id == product_id,
                            ads.c.status == "approved",
                            PASSED,
                        )
                        .order_by(ads.c.approved_at.desc(), ads.c.id.desc())
                    )
                )
                .mappings()
                .all()
            )
        return list(rows)


def export_package(ad: Ad, details: Row, image: Blob, *, label: bool) -> bytes:
    """FR-015: the image as published, the copy with its link, and ``provenance.json``."""
    published = publishable(image, label=label)
    provenance = provenance_of(image)
    record: dict[str, Any] = (
        provenance_record(
            provenance, creative_id=str(ad.creative_id), image_format=details["format"]
        )
        if provenance is not None
        else {
            "creative_id": str(ad.creative_id),
            "format": details["format"],
            "ai_generated": "true",
        }
    )
    record["label"] = AI_LABEL if label else ""
    record["label_applied"] = "true" if label else "false"
    verification = details["verification"] or {}
    report = {
        "ad": {"id": str(ad.id), "code": ad.code, "wa_link": ad.wa_link, "status": ad.status},
        "creatives": [record],
        "verification": {
            key: verification[key]
            for key in ("status", "mask_iou", "color_delta", "checked_at")
            if key in verification
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{ad.code or 'borrador'}-{details['format']}.png", published.data)
        archive.writestr("copy.txt", fill_link(details["copy_text"], ad.wa_link) + "\n")
        archive.writestr("provenance.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return buffer.getvalue()


def _folded(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def rank_by_hint(rows: list[Row], variant_hint: Mapping[str, str] | None) -> list[Row]:
    """Ads whose copy or scene mention more of the hint's values first; newest among equals."""
    values = [_folded(value) for value in (variant_hint or {}).values() if value.strip()]
    if not values:
        return rows

    def matches(row: Row) -> int:
        haystack = _folded(f"{row['copy_text']} {row['scene_prompt'] or ''}")
        return sum(value in haystack for value in values)

    return sorted(rows, key=matches, reverse=True)  # sorted is stable: newest stays first


async def get_approved_asset(
    seller_id: UUID,
    product_id: UUID,
    *,
    store: AdStore,
    storage: BlobStore,
    sellers: SellerStore,
    ad_id: UUID | None = None,
    variant_hint: Mapping[str, str] | None = None,
) -> ApprovedAsset | None:
    """I-10 for ``send_ad_asset``: the ad to send, or None (``no_approved_ad``).

    ``ad_id`` picks one ad (it must be approved and of this product); otherwise the newest
    approved ad that best matches ``variant_hint``. The image carries the AI label unless the
    seller turned it off (Art. IV.3).
    """
    rows = await store.approved_for(seller_id, product_id)
    if ad_id is not None:
        rows = [row for row in rows if row["id"] == ad_id]
    ranked = rank_by_hint(rows, variant_hint)
    if not ranked:
        return None
    chosen = ranked[0]
    settings = await sellers.settings(seller_id)
    label = settings.ai_label_enabled if settings is not None else True
    image = publishable(await storage.get(chosen["storage_key"]), label=label)
    return ApprovedAsset(
        ad_id=chosen["id"],
        code=chosen["code"],
        image=image,
        caption=fill_link(chosen["copy_text"], chosen["wa_link"]),
        wa_link=chosen["wa_link"],
    )
