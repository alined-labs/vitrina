"""In-memory catalog for tests and local work without a database; same rules as Postgres."""

import asyncio
import math
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import MISSING

from vitrina_catalog.domain import (
    RESERVATION_TTL,
    Citation,
    FactKind,
    FactStatus,
    ProductDetail,
    ProductNotFoundError,
    ProductStatus,
    Reservation,
    SearchKind,
    SearchResult,
    StockLevel,
)
from vitrina_catalog.schemas import (
    Fact,
    FactCreate,
    FactUpdate,
    Product,
    ProductCreate,
    ProductUpdate,
)
from vitrina_catalog.text import describe_product, product_amounts, tokens
from vitrina_core.http import decode_cursor, encode_cursor
from vitrina_core.ids import new_id
from vitrina_core.money import amounts_cop

# Crockford base32 without I, L, O, U: codes such as R-4F2K stay readable over the phone.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@dataclass(frozen=True)
class _Product:
    id: UUID
    seller_id: UUID
    name: str
    price_cop: int
    stock: int | None
    status: ProductStatus
    description: str | None
    attributes: dict[str, str]
    amounts_cop: tuple[int, ...]
    image_embedding: tuple[float, ...] | None
    source: str
    created_at: datetime
    updated_at: datetime

    @property
    def text(self) -> str:
        return describe_product(self.name, self.description, self.price_cop, self.attributes)


@dataclass(frozen=True)
class _Fact:
    id: UUID
    seller_id: UUID
    kind: FactKind
    text: str
    status: FactStatus
    amounts_cop: tuple[int, ...]
    source: str | None
    created_at: datetime


@dataclass(frozen=True)
class _Hold:
    id: UUID
    seller_id: UUID
    product_id: UUID
    quantity: int
    code: str
    expires_at: datetime


def _reservation_code() -> str:
    return "R-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _overlap(query: set[str], text: str) -> float:
    return len(query & tokens(text)) / len(query) if query else 0.0


def _page[T](
    rows: list[T], key: Callable[[T], tuple[datetime, UUID]], limit: int, cursor: str | None
) -> tuple[list[T], str | None]:
    """Newest first on (created_at, id), the same order and cursors as the database."""
    ordered = sorted(rows, key=key, reverse=True)
    if cursor is not None:
        position = decode_cursor(cursor)
        ordered = [row for row in ordered if key(row) < position]
    items = ordered[:limit]
    more = len(ordered) > limit
    return items, encode_cursor(*key(items[-1])) if more and items else None


def _sent(values: Mapping[str, Any]) -> dict[str, Any]:
    """Fields the client actually sent (MISSING means: leave it as it is)."""
    return {key: value for key, value in values.items() if value is not MISSING}


@dataclass
class InMemoryCatalog:
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _products: dict[UUID, _Product] = field(default_factory=dict)
    _facts: dict[UUID, _Fact] = field(default_factory=dict)
    _holds: list[_Hold] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── seeding (tests, local demos) ──

    def add_product(
        self,
        seller_id: UUID,
        name: str,
        *,
        price_cop: int,
        stock: int | None = None,
        status: ProductStatus = "active",
        description: str | None = None,
        attributes: Mapping[str, str] | None = None,
        amounts_cop: Sequence[int] = (),
        image_embedding: Sequence[float] | None = None,
    ) -> UUID:
        now = self.clock()
        product = _Product(
            id=new_id(),
            seller_id=seller_id,
            name=name,
            price_cop=price_cop,
            stock=stock,
            status=status,
            description=description,
            attributes=dict(attributes or {}),
            amounts_cop=tuple(sorted({price_cop, *amounts_cop})),
            image_embedding=tuple(image_embedding) if image_embedding is not None else None,
            source="manual",
            created_at=now,
            updated_at=now,
        )
        self._products[product.id] = product
        return product.id

    def add_fact(
        self,
        seller_id: UUID,
        kind: FactKind,
        text: str,
        *,
        status: FactStatus = "active",
        amounts_cop: Sequence[int] = (),
    ) -> UUID:
        fact = _Fact(
            new_id(),
            seller_id,
            kind,
            text,
            status,
            tuple(sorted(set(amounts_cop))),
            None,
            self.clock(),
        )
        self._facts[fact.id] = fact
        return fact.id

    # ── CatalogStore (routes) ──

    def _view(self, product: _Product) -> Product:
        optional: dict[str, Any] = {}
        if product.description:
            optional["description"] = product.description
        if product.attributes:
            optional["attributes"] = product.attributes
        return Product(
            id=product.id,
            seller_id=product.seller_id,
            name=product.name,
            price_cop=product.price_cop,
            stock=product.stock,
            reserved=self._reserved(product),
            status=product.status,
            source="image_import" if product.source == "image_import" else "manual",
            created_at=product.created_at,
            updated_at=product.updated_at,
            **optional,
        )

    def _owned_product(self, seller_id: UUID, product_id: UUID) -> _Product | None:
        product = self._products.get(product_id)
        return product if product is not None and product.seller_id == seller_id else None

    async def create_product(self, seller_id: UUID, data: ProductCreate) -> Product:
        values = _sent(data.model_dump())
        product_id = self.add_product(
            seller_id,
            values["name"],
            price_cop=values["price_cop"],
            stock=values.get("stock"),
            status=values.get("status", "active"),
            description=values.get("description"),
            attributes=values.get("attributes"),
            amounts_cop=product_amounts(
                values["price_cop"], values.get("description"), values.get("attributes")
            ),
        )
        return self._view(self._products[product_id])

    async def product(self, seller_id: UUID, product_id: UUID) -> Product | None:
        product = self._owned_product(seller_id, product_id)
        return self._view(product) if product is not None else None

    async def list_products(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        status: ProductStatus | None = None,
        q: str | None = None,
    ) -> tuple[list[Product], str | None]:
        words = tokens(q or "")
        rows = [
            product
            for product in self._products.values()
            if product.seller_id == seller_id
            and (status is None or product.status == status)
            and (
                not words
                or _overlap(words, f"{product.text} {' '.join(product.attributes.values())}") > 0
            )
        ]
        items, next_cursor = _page(rows, lambda p: (p.created_at, p.id), limit, cursor)
        return [self._view(product) for product in items], next_cursor

    async def update_product(
        self, seller_id: UUID, product_id: UUID, data: ProductUpdate
    ) -> Product | None:
        product = self._owned_product(seller_id, product_id)
        if product is None:
            return None
        updated = replace(product, **_sent(data.model_dump()), updated_at=self.clock())
        updated = replace(
            updated,
            amounts_cop=product_amounts(updated.price_cop, updated.description, updated.attributes),
        )
        self._products[product_id] = updated
        return self._view(updated)

    async def archive_product(self, seller_id: UUID, product_id: UUID) -> bool:
        product = self._owned_product(seller_id, product_id)
        if product is None:
            return False
        self._products[product_id] = replace(product, status="archived", updated_at=self.clock())
        return True

    def _fact_view(self, fact: _Fact) -> Fact:
        optional: dict[str, Any] = {"source": fact.source} if fact.source else {}
        return Fact(
            id=fact.id,
            seller_id=fact.seller_id,
            kind=fact.kind,
            text=fact.text,
            status=fact.status,
            amounts_cop=list(fact.amounts_cop),
            created_at=fact.created_at,
            **optional,
        )

    def _owned_fact(self, seller_id: UUID, fact_id: UUID) -> _Fact | None:
        fact = self._facts.get(fact_id)
        return fact if fact is not None and fact.seller_id == seller_id else None

    async def create_fact(self, seller_id: UUID, data: FactCreate) -> Fact:
        values = _sent(data.model_dump())
        fact_id = self.add_fact(
            seller_id, values["kind"], values["text"], amounts_cop=amounts_cop(values["text"])
        )
        if values.get("source"):
            self._facts[fact_id] = replace(self._facts[fact_id], source=values["source"])
        return self._fact_view(self._facts[fact_id])

    async def list_facts(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        kind: FactKind | None = None,
    ) -> tuple[list[Fact], str | None]:
        rows = [
            f
            for f in self._facts.values()
            if f.seller_id == seller_id and (kind is None or f.kind == kind)
        ]
        items, next_cursor = _page(rows, lambda f: (f.created_at, f.id), limit, cursor)
        return [self._fact_view(fact) for fact in items], next_cursor

    async def update_fact(self, seller_id: UUID, fact_id: UUID, data: FactUpdate) -> Fact | None:
        fact = self._owned_fact(seller_id, fact_id)
        if fact is None:
            return None
        updated = replace(fact, **_sent(data.model_dump()))
        updated = replace(updated, amounts_cop=tuple(sorted(set(amounts_cop(updated.text)))))
        self._facts[fact_id] = updated
        return self._fact_view(updated)

    async def delete_fact(self, seller_id: UUID, fact_id: UUID) -> bool:
        if self._owned_fact(seller_id, fact_id) is None:
            return False
        del self._facts[fact_id]
        return True

    # ── Catalog protocol (agent tools) ──

    def _active_product(self, seller_id: UUID, product_id: UUID) -> _Product:
        product = self._owned_product(seller_id, product_id)
        if product is None or product.status != "active":
            raise ProductNotFoundError(f"no active product {product_id} for this seller")
        return product

    def _reserved(self, product: _Product) -> int:
        now = self.clock()
        return sum(
            hold.quantity
            for hold in self._holds
            if hold.product_id == product.id and hold.expires_at > now
        )

    def _available(self, product: _Product) -> int | None:
        return None if product.stock is None else max(product.stock - self._reserved(product), 0)

    async def search(
        self,
        seller_id: UUID,
        query: str,
        *,
        kinds: Sequence[SearchKind] = (),
        top_k: int = 5,
        image_embedding: Sequence[float] | None = None,
    ) -> list[SearchResult]:
        wanted = set(kinds)
        words = tokens(query)
        hits: list[SearchResult] = []
        if not wanted or "product" in wanted:
            for product in self._products.values():
                if product.seller_id != seller_id or product.status != "active":
                    continue
                score = _overlap(words, f"{product.text} {' '.join(product.attributes.values())}")
                if image_embedding is not None and product.image_embedding is not None:
                    score += _cosine(image_embedding, product.image_embedding)
                if score > 0:
                    hits.append(
                        SearchResult(
                            id=product.id,
                            kind="product",
                            text=product.text,
                            price_cop=product.price_cop,
                            stock=self._available(product),
                            score=round(score, 4),
                        )
                    )
        for fact in self._facts.values():
            if (
                fact.seller_id != seller_id
                or fact.status != "active"
                or (wanted and fact.kind not in wanted)
            ):
                continue
            score = _overlap(words, fact.text)
            if score > 0:
                hits.append(
                    SearchResult(id=fact.id, kind=fact.kind, text=fact.text, score=round(score, 4))
                )
        hits.sort(key=lambda hit: (-hit.score, hit.text))
        return hits[:top_k]

    async def get_product(self, seller_id: UUID, product_id: UUID) -> ProductDetail:
        product = self._active_product(seller_id, product_id)
        return ProductDetail(
            id=product.id,
            name=product.name,
            description=product.description,
            price_cop=product.price_cop,
            stock=product.stock,
            available=self._available(product),
            attributes=product.attributes,
        )

    async def check_stock(
        self, seller_id: UUID, product_id: UUID, variant: Mapping[str, str] | None = None
    ) -> StockLevel:
        # Stock is per product in data-model.md; the variant only informs the reply.
        product = self._active_product(seller_id, product_id)
        return StockLevel(
            product_id=product.id,
            available=self._available(product),
            tracked=product.stock is not None,
        )

    async def reserve(
        self,
        seller_id: UUID,
        conversation_id: UUID,
        product_id: UUID,
        quantity: int,
        *,
        variant: Mapping[str, str] | None = None,
        note: str | None = None,
    ) -> Reservation:
        async with self._lock:
            product = self._active_product(seller_id, product_id)
            available = self._available(product)
            if available is None:
                return Reservation(status="not_tracked")
            if quantity > available:
                return Reservation(status="insufficient_stock")
            taken = {hold.code for hold in self._holds if hold.seller_id == seller_id}
            code = _reservation_code()
            while code in taken:
                code = _reservation_code()
            hold = _Hold(
                new_id(), seller_id, product.id, quantity, code, self.clock() + RESERVATION_TTL
            )
            self._holds.append(hold)
            return Reservation(
                reservation_id=hold.id,
                code=hold.code,
                expires_at=hold.expires_at,
                status="reserved",
            )

    async def citations(self, seller_id: UUID, ids: Sequence[UUID]) -> list[Citation]:
        found: list[Citation] = []
        for item_id in ids:
            product = self._owned_product(seller_id, item_id)
            if product is not None and product.status == "active":
                found.append(
                    Citation(id=product.id, kind="product", amounts_cop=product.amounts_cop)
                )
                continue
            fact = self._owned_fact(seller_id, item_id)
            if fact is not None and fact.status == "active":
                found.append(Citation(id=fact.id, kind=fact.kind, amounts_cop=fact.amounts_cop))
        return found
