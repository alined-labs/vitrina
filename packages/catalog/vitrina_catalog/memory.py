"""In-memory ``Catalog`` for tests and local development; same rules as the database one."""

import asyncio
import math
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

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
from vitrina_catalog.text import describe_product, tokens

# Crockford base32 without I, L, O, U: codes such as R-4F2K stay readable over the phone.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@dataclass
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

    @property
    def text(self) -> str:
        return describe_product(self.name, self.description, self.price_cop, self.attributes)


@dataclass
class _Fact:
    id: UUID
    seller_id: UUID
    kind: FactKind
    text: str
    status: FactStatus
    amounts_cop: tuple[int, ...]


@dataclass
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
        product = _Product(
            id=uuid4(),
            seller_id=seller_id,
            name=name,
            price_cop=price_cop,
            stock=stock,
            status=status,
            description=description,
            attributes=dict(attributes or {}),
            amounts_cop=tuple(sorted({price_cop, *amounts_cop})),
            image_embedding=tuple(image_embedding) if image_embedding is not None else None,
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
        fact = _Fact(uuid4(), seller_id, kind, text, status, tuple(sorted(set(amounts_cop))))
        self._facts[fact.id] = fact
        return fact.id

    # ── Catalog protocol ──

    def _active_product(self, seller_id: UUID, product_id: UUID) -> _Product:
        product = self._products.get(product_id)
        if product is None or product.seller_id != seller_id or product.status != "active":
            raise ProductNotFoundError(f"no active product {product_id} for this seller")
        return product

    def _available(self, product: _Product) -> int | None:
        if product.stock is None:
            return None
        now = self.clock()
        held = sum(
            hold.quantity
            for hold in self._holds
            if hold.product_id == product.id and hold.expires_at > now
        )
        return max(product.stock - held, 0)

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
                uuid4(), seller_id, product.id, quantity, code, self.clock() + RESERVATION_TTL
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
            product = self._products.get(item_id)
            if (
                product is not None
                and product.seller_id == seller_id
                and product.status == "active"
            ):
                found.append(
                    Citation(id=product.id, kind="product", amounts_cop=product.amounts_cop)
                )
                continue
            fact = self._facts.get(item_id)
            if fact is not None and fact.seller_id == seller_id and fact.status == "active":
                found.append(Citation(id=fact.id, kind=fact.kind, amounts_cop=fact.amounts_cop))
        return found
