"""openapi.yaml ``AttributionReport`` and ``ObjectionReport`` (T044, FR-041)."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

# openapi.yaml ``CopyAngle``.
CopyAngle = Literal["price", "quality", "handmade", "urgency", "gift", "local_pride"]


class AdAttribution(BaseModel):
    ad_id: UUID
    code: str
    angle: CopyAngle | MISSING = MISSING
    chats: int
    reservations: int
    bookings: int
    handoffs: int
    conversion_rate: float = Field(description="(reservations + bookings) / chats")
    objections_by_tag: dict[str, int] | MISSING = MISSING


class AngleAttribution(BaseModel):
    chats: int | MISSING = MISSING
    conversion_rate: float | MISSING = MISSING


class AttributionReport(BaseModel):
    period_from: date
    period_to: date
    ads: list[AdAttribution]
    by_angle: dict[str, AngleAttribution]
    unattributed_chats: int | MISSING = MISSING


class AdObjections(BaseModel):
    ad_id: UUID | MISSING = MISSING
    code: str | MISSING = MISSING
    tags: dict[str, int] | MISSING = MISSING
    sample_quotes_redacted: Annotated[list[str], Field(max_length=3)] | MISSING = MISSING


class ObjectionReport(BaseModel):
    period_from: date
    period_to: date
    totals: dict[str, int]
    by_ad: list[AdObjections]
