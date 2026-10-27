"""Catalog resources as the HTTP contract defines them (openapi.yaml ``Product*``, ``Fact*``)."""

from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

from vitrina_catalog.domain import FactKind, FactStatus, ProductStatus

Name = Annotated[str, Field(max_length=120)]
Description = Annotated[str, Field(max_length=2000)]
Price = Annotated[int, Field(ge=0)]
Stock = Annotated[int, Field(ge=0)]
FactText = Annotated[str, Field(max_length=1000)]
# data-model.md products.source: drafts from a photo are image_import (FR-003).
ProductSource = Literal["manual", "image_import"]


class ProductCreate(BaseModel):
    name: Name
    description: Description | MISSING = MISSING
    price_cop: Price
    stock: Stock | None = None
    attributes: dict[str, str] | MISSING = MISSING
    status: ProductStatus | MISSING = MISSING


class ProductUpdate(BaseModel):
    name: Name | MISSING = MISSING
    description: Description | MISSING = MISSING
    price_cop: Price | MISSING = MISSING
    stock: Stock | MISSING | None = MISSING
    attributes: dict[str, str] | MISSING = MISSING
    status: ProductStatus | MISSING = MISSING


class Product(BaseModel):
    id: UUID
    seller_id: UUID
    name: str
    description: str | MISSING = MISSING
    price_cop: int
    stock: int | None = None
    reserved: int | MISSING = MISSING
    attributes: dict[str, str] | MISSING = MISSING
    status: ProductStatus
    # Assets belong to creative; the router adds them once creative.api lists them.
    asset_ids: list[UUID] | MISSING = MISSING
    source: ProductSource = "manual"
    created_at: datetime
    updated_at: datetime | MISSING = MISSING


class FactCreate(BaseModel):
    kind: FactKind
    text: FactText
    source: Annotated[str, Field(max_length=120)] | MISSING = MISSING


class FactUpdate(BaseModel):
    kind: FactKind | MISSING = MISSING
    text: FactText | MISSING = MISSING
    status: FactStatus | MISSING = MISSING


class Fact(BaseModel):
    id: UUID
    seller_id: UUID
    kind: FactKind
    text: str
    source: str | MISSING = MISSING
    status: FactStatus
    amounts_cop: list[int] | MISSING = MISSING
    created_at: datetime


class CatalogStore(Protocol):
    """The catalog's own CRUD, behind the /products and /facts routes (FR-002)."""

    async def create_product(
        self, seller_id: UUID, data: ProductCreate, *, source: ProductSource = "manual"
    ) -> Product: ...

    async def product(self, seller_id: UUID, product_id: UUID) -> Product | None: ...

    async def list_products(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        status: ProductStatus | None = None,
        q: str | None = None,
    ) -> tuple[list[Product], str | None]: ...

    async def update_product(
        self, seller_id: UUID, product_id: UUID, data: ProductUpdate
    ) -> Product | None: ...

    async def archive_product(self, seller_id: UUID, product_id: UUID) -> bool: ...

    async def create_fact(self, seller_id: UUID, data: FactCreate) -> Fact: ...

    async def list_facts(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        kind: FactKind | None = None,
    ) -> tuple[list[Fact], str | None]: ...

    async def update_fact(
        self, seller_id: UUID, fact_id: UUID, data: FactUpdate
    ) -> Fact | None: ...

    async def delete_fact(self, seller_id: UUID, fact_id: UUID) -> bool: ...
