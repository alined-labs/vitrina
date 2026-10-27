"""Sellers: the tenant behind every request (T022; AS-1.1; FR-001; openapi.yaml ``Seller*``)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import sellers
from vitrina_core.ids import new_id

DEFAULT_CONSENT_PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"

Clock = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
# Weekday → list of [open, close] in local time; a missing weekday is closed.
BusinessHours = dict[str, list[Annotated[list[Clock], Field(min_length=2, max_length=2)]]]


class BrandVoiceProfile(BaseModel):
    formality: Annotated[float, Field(ge=1, le=5)] | MISSING = MISSING
    warmth: Annotated[float, Field(ge=1, le=5)] | MISSING = MISSING
    emoji_rate: Annotated[float, Field(ge=0, le=1)] | MISSING = MISSING
    regionalisms: list[str] | MISSING = MISSING
    greeting_style: str | MISSING = MISSING
    sample_count: int | MISSING = MISSING
    updated_at: datetime | MISSING = MISSING


class SellerCreate(BaseModel):
    business_name: Annotated[str, Field(max_length=120)]
    owner_name: Annotated[str, Field(max_length=120)]
    whatsapp_number: Annotated[str, Field(pattern=r"^\+[1-9]\d{7,14}$")]
    city: Annotated[str, Field(max_length=80)]
    timezone: str = "America/Bogota"
    category: Literal["products", "services"] = "products"


class SellerUpdate(BaseModel):
    business_name: Annotated[str, Field(max_length=120)] | MISSING = MISSING
    owner_name: Annotated[str, Field(max_length=120)] | MISSING = MISSING
    city: Annotated[str, Field(max_length=80)] | MISSING = MISSING
    business_hours: BusinessHours | MISSING = MISSING
    handoff_amount_threshold_cop: Annotated[int, Field(ge=0)] | MISSING = MISSING
    brand_voice_min: Annotated[float, Field(ge=1, le=5)] | MISSING = MISSING
    ai_label_enabled: bool | MISSING = MISSING
    handoff_summary_enabled: bool | MISSING = MISSING
    consent_prompt_text: Annotated[str, Field(max_length=300)] | MISSING = MISSING


class Seller(BaseModel):
    id: UUID
    business_name: str
    owner_name: str
    whatsapp_number: str
    city: str
    timezone: str
    category: Literal["products", "services"] | MISSING = MISSING
    business_hours: BusinessHours | MISSING = MISSING
    handoff_amount_threshold_cop: int = 300000
    brand_voice_min: float = 3.5
    brand_voice_profile: BrandVoiceProfile | MISSING = MISSING
    ai_label_enabled: bool = True
    handoff_summary_enabled: bool = True
    consent_prompt_text: str = DEFAULT_CONSENT_PROMPT
    marketing_template_status: Literal["none", "pending", "approved", "rejected"] | MISSING = (
        MISSING
    )
    created_at: datetime


class SellerSettings(BaseModel):
    """What the conversation engine reads per turn, including fields the public Seller omits."""

    seller_id: UUID
    business_name: str
    owner_name: str
    whatsapp_number: str
    timezone: str
    category: Literal["products", "services"]
    business_hours: BusinessHours
    handoff_amount_threshold_cop: int
    low_confidence_threshold: float
    brand_voice_min: float
    brand_voice_profile: BrandVoiceProfile
    ai_label_enabled: bool
    handoff_summary_enabled: bool
    consent_prompt_text: str


def _settings(row: dict[str, Any]) -> SellerSettings:
    return SellerSettings(
        seller_id=row["id"],
        business_name=row["business_name"],
        owner_name=row["owner_name"],
        whatsapp_number=row["whatsapp_number"],
        timezone=row["timezone"],
        category=row["category"],
        business_hours=row.get("business_hours") or {},
        handoff_amount_threshold_cop=int(row["handoff_amount_threshold_cop"]),
        low_confidence_threshold=float(row.get("low_confidence_threshold", 0.6)),
        brand_voice_min=float(row["brand_voice_min"]),
        brand_voice_profile=BrandVoiceProfile.model_validate(row.get("brand_voice_profile") or {}),
        ai_label_enabled=bool(row["ai_label_enabled"]),
        handoff_summary_enabled=bool(row["handoff_summary_enabled"]),
        consent_prompt_text=row["consent_prompt_text"],
    )


class SellerConflictError(Exception):
    """The WhatsApp number or the signed-in account already has a seller."""


class SellerStore(Protocol):
    async def create(self, data: SellerCreate, *, auth_subject: str | None) -> Seller: ...

    async def get(self, seller_id: UUID) -> Seller | None: ...

    async def update(self, seller_id: UUID, data: SellerUpdate) -> Seller | None: ...

    async def set_voice_profile(self, seller_id: UUID, profile: BrandVoiceProfile) -> None: ...

    async def settings(self, seller_id: UUID) -> SellerSettings | None: ...


def _sent(model: BaseModel) -> dict[str, Any]:
    return {key: value for key, value in model.model_dump().items() if value is not MISSING}


def _view(row: dict[str, Any]) -> Seller:
    profile = row.get("brand_voice_profile") or {}
    values = {key: row[key] for key in Seller.model_fields if key in row and row[key] is not None}
    if not profile:
        values.pop("brand_voice_profile", None)
    if not row.get("business_hours"):
        values.pop("business_hours", None)
    for key in ("handoff_amount_threshold_cop",):
        if key in values:
            values[key] = int(values[key])
    if "brand_voice_min" in values:
        values["brand_voice_min"] = float(values["brand_voice_min"])
    return Seller.model_validate(values)


@dataclass
class InMemorySellerStore:
    """Sellers without a database; also the auth directory (subject → seller) in that mode."""

    rows: dict[UUID, dict[str, Any]] = field(default_factory=dict)

    async def create(
        self, data: SellerCreate, *, auth_subject: str | None, seller_id: UUID | None = None
    ) -> Seller:
        """``seller_id`` fixes the id, for fixtures that must be the same on every run."""
        for row in self.rows.values():
            if row["whatsapp_number"] == data.whatsapp_number or (
                auth_subject and row["auth_subject"] == auth_subject
            ):
                raise SellerConflictError("this WhatsApp number or account already has a seller")
        row = {
            **data.model_dump(),
            "id": seller_id or new_id(),
            "auth_subject": auth_subject,
            "business_hours": {},
            "handoff_amount_threshold_cop": 300000,
            "brand_voice_min": 3.5,
            "low_confidence_threshold": 0.6,
            "brand_voice_profile": {},
            "ai_label_enabled": True,
            "handoff_summary_enabled": True,
            "consent_prompt_text": DEFAULT_CONSENT_PROMPT,
            "marketing_template_status": "none",
            "created_at": datetime.now(UTC),
        }
        self.rows[row["id"]] = row
        return _view(row)

    async def get(self, seller_id: UUID) -> Seller | None:
        row = self.rows.get(seller_id)
        return _view(row) if row is not None else None

    async def update(self, seller_id: UUID, data: SellerUpdate) -> Seller | None:
        row = self.rows.get(seller_id)
        if row is None:
            return None
        row.update(_sent(data))
        return _view(row)

    async def set_voice_profile(self, seller_id: UUID, profile: BrandVoiceProfile) -> None:
        self.rows[seller_id]["brand_voice_profile"] = profile.model_dump(mode="json")

    async def settings(self, seller_id: UUID) -> SellerSettings | None:
        row = self.rows.get(seller_id)
        return _settings(row) if row is not None else None

    async def seller_for_subject(self, subject: str) -> UUID | None:
        return next(
            (row["id"] for row in self.rows.values() if row["auth_subject"] == subject), None
        )


class PostgresSellerStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create(self, data: SellerCreate, *, auth_subject: str | None) -> Seller:
        values = {
            **data.model_dump(),
            "id": new_id(),
            "auth_subject": auth_subject,
            "consent_prompt_text": DEFAULT_CONSENT_PROMPT,
        }
        try:
            async with self.sessions() as session, session.begin():
                row = (
                    (await session.execute(sa.insert(sellers).values(values).returning(*sellers.c)))
                    .mappings()
                    .one()
                )
        except IntegrityError as error:
            raise SellerConflictError(
                "this WhatsApp number or account already has a seller"
            ) from error
        return _view(dict(row))

    async def get(self, seller_id: UUID) -> Seller | None:
        async with self.sessions() as session:
            row = (
                (await session.execute(sa.select(sellers).where(sellers.c.id == seller_id)))
                .mappings()
                .first()
            )
        return _view(dict(row)) if row is not None else None

    async def update(self, seller_id: UUID, data: SellerUpdate) -> Seller | None:
        changes = _sent(data)
        async with self.sessions() as session, session.begin():
            statement = sa.update(sellers).where(sellers.c.id == seller_id).returning(*sellers.c)
            if changes:
                row = (await session.execute(statement.values(changes))).mappings().first()
            else:
                row = (
                    (await session.execute(sa.select(sellers).where(sellers.c.id == seller_id)))
                    .mappings()
                    .first()
                )
        return _view(dict(row)) if row is not None else None

    async def set_voice_profile(self, seller_id: UUID, profile: BrandVoiceProfile) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(sellers)
                .where(sellers.c.id == seller_id)
                .values(brand_voice_profile=profile.model_dump(mode="json"))
            )

    async def settings(self, seller_id: UUID) -> SellerSettings | None:
        async with self.sessions() as session:
            row = (
                (await session.execute(sa.select(sellers).where(sellers.c.id == seller_id)))
                .mappings()
                .first()
            )
        return _settings(dict(row)) if row is not None else None
