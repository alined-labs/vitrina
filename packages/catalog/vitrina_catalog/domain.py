"""Types and the protocol behind ``vitrina_catalog.api``."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

FactKind = Literal["faq", "shipping", "payment", "policy", "hours", "other"]
SearchKind = Literal["product", "faq", "shipping", "payment", "policy", "hours", "other"]
ProductStatus = Literal["draft", "active", "archived"]
FactStatus = Literal["draft", "active"]

RESERVATION_TTL = timedelta(hours=24)


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class SearchResult(_View):
    """One hit, shaped like the ``search_catalog`` tool result; ``id`` is what the agent cites."""

    id: UUID
    kind: SearchKind
    text: str
    price_cop: int | None = None
    stock: int | None = None
    score: float


class ProductDetail(_View):
    """The ``get_product`` tool result without ``approved_ad_ids``, which the engine adds."""

    id: UUID
    name: str
    description: str | None = None
    price_cop: int
    stock: int | None
    available: int | None
    attributes: dict[str, str]
    status: Literal["active"] = "active"


class StockLevel(_View):
    """``check_stock``: stock minus active reservations; ``tracked=False`` means never promise."""

    product_id: UUID
    available: int | None
    tracked: bool


class Reservation(_View):
    """``reserve_item``: ids, code and expiry are set only when ``status == "reserved"``."""

    reservation_id: UUID | None = None
    code: str | None = None
    expires_at: datetime | None = None
    status: Literal["reserved", "insufficient_stock", "not_tracked"]


class Citation(_View):
    """What the price validator needs about a cited id (R2, data-model.md invariants 6 and 7)."""

    id: UUID
    kind: SearchKind
    amounts_cop: tuple[int, ...]


class ProductNotFoundError(LookupError):
    """No active product with that id for this seller."""


class Catalog(Protocol):
    async def search(
        self,
        seller_id: UUID,
        query: str,
        *,
        kinds: Sequence[SearchKind] = (),
        top_k: int = 5,
        image_embedding: Sequence[float] | None = None,
    ) -> list[SearchResult]: ...

    async def get_product(self, seller_id: UUID, product_id: UUID) -> ProductDetail: ...

    async def check_stock(
        self, seller_id: UUID, product_id: UUID, variant: Mapping[str, str] | None = None
    ) -> StockLevel: ...

    async def reserve(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        product_id: UUID,
        quantity: int,
        *,
        variant: Mapping[str, str] | None = None,
        note: str | None = None,
    ) -> Reservation: ...

    async def citations(self, seller_id: UUID, ids: Sequence[UUID]) -> list[Citation]: ...
