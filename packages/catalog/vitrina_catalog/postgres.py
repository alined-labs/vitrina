"""The catalog on Postgres + pgvector (T020; plan.md §4.1; FR-002, FR-003; R1).

Retrieval fuses three ranked lists with reciprocal-rank fusion: vector similarity (when an
embedder is configured), Spanish full text (accent-folded, any query word may match) and
trigram similarity on product names. Without embeddings it is keyword search, which is
what replay mode and machines without the embedding model get. Only ``active`` rows are
retrievable or citable, always for one seller.
"""

import logging
import secrets
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.domain import (
    RESERVATION_TTL,
    Citation,
    FactKind,
    ProductDetail,
    ProductNotFoundError,
    ProductStatus,
    Reservation,
    SearchKind,
    SearchResult,
    StockLevel,
)
from vitrina_catalog.memory import CODE_ALPHABET
from vitrina_catalog.schemas import (
    Fact,
    FactCreate,
    FactUpdate,
    Product,
    ProductCreate,
    ProductSource,
    ProductUpdate,
)
from vitrina_catalog.tables import facts, products, reservations
from vitrina_catalog.text import describe_product, named_kinds, product_amounts, tokens
from vitrina_core.http import keyset_page, next_cursor
from vitrina_core.ids import new_id
from vitrina_core.money import amounts_cop
from vitrina_core.providers import Embedder, EmbeddingRequest, ProviderError

logger = logging.getLogger("vitrina.catalog")

TEXT_DIMENSIONS = 1024  # data-model.md: embedding vector(1024)
CANDIDATES = 20
RRF_K = 60
NAME_SIMILARITY = 0.5


def _sent(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not MISSING}


def _reserved(product_id: Any) -> sa.ScalarSelect[Any]:
    return (
        sa.select(sa.func.coalesce(sa.func.sum(reservations.c.quantity), 0))
        .where(
            reservations.c.product_id == product_id,
            reservations.c.status == "reserved",
            reservations.c.expires_at > sa.func.now(),
        )
        .scalar_subquery()
    )


def _product_view(row: Mapping[Any, Any]) -> Product:
    optional: dict[str, Any] = {}
    if row["description"]:
        optional["description"] = row["description"]
    if row["attributes"]:
        optional["attributes"] = row["attributes"]
    return Product(
        id=row["id"],
        seller_id=row["seller_id"],
        name=row["name"],
        price_cop=row["price_cop"],
        stock=row["stock"],
        reserved=int(row["reserved"]),
        status=row["status"],
        source="image_import" if row["source"] == "image_import" else "manual",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        **optional,
    )


def _fact_view(row: Mapping[Any, Any]) -> Fact:
    optional: dict[str, Any] = {"source": row["source"]} if row["source"] else {}
    return Fact(
        id=row["id"],
        seller_id=row["seller_id"],
        kind=row["kind"],
        text=row["text"],
        status=row["status"],
        amounts_cop=list(row["amounts_cop"]),
        created_at=row["created_at"],
        **optional,
    )


class PostgresCatalog:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        embedder: Embedder | None = None,
        embed_model: str | None = None,
    ) -> None:
        self.sessions = sessions
        self.embedder = embedder
        self.embed_model = embed_model

    async def _embed(self, text: str) -> list[float] | None:
        """Best effort: a missing model or a provider outage leaves keyword search working."""
        if self.embedder is None or not self.embed_model or not text.strip():
            return None
        try:
            response = await self.embedder.embed(
                EmbeddingRequest(model=self.embed_model, texts=(text,))
            )
        except ProviderError as error:
            logger.warning("embedding skipped: %s", error)
            return None
        vector = list(response.vectors[0])
        if len(vector) != TEXT_DIMENSIONS:
            logger.warning(
                "embedding skipped: %d dimensions, the schema stores %d",
                len(vector),
                TEXT_DIMENSIONS,
            )
            return None
        return vector

    # ── CatalogStore (routes) ──

    def _products_with_reserved(self) -> sa.Select[Any]:
        return sa.select(*products.c, _reserved(products.c.id).label("reserved"))

    async def create_product(
        self, seller_id: UUID, data: ProductCreate, *, source: ProductSource = "manual"
    ) -> Product:
        values = _sent(data.model_dump())
        description, attributes = values.get("description"), values.get("attributes") or {}
        text = describe_product(values["name"], description, values["price_cop"], attributes)
        row = {
            "id": new_id(),
            "seller_id": seller_id,
            "name": values["name"],
            "description": description,
            "price_cop": values["price_cop"],
            "stock": values.get("stock"),
            "attributes": attributes,
            "status": values.get("status", "active"),
            "source": source,
            "amounts_cop": list(product_amounts(values["price_cop"], description, attributes)),
            "embedding": await self._embed(text),
        }
        async with self.sessions() as session, session.begin():
            created = (
                (await session.execute(sa.insert(products).values(row).returning(*products.c)))
                .mappings()
                .one()
            )
        return _product_view({**created, "reserved": 0})

    async def product(self, seller_id: UUID, product_id: UUID) -> Product | None:
        statement = self._products_with_reserved().where(
            products.c.id == product_id, products.c.seller_id == seller_id
        )
        async with self.sessions() as session:
            row = (await session.execute(statement)).mappings().first()
        return _product_view(row) if row is not None else None

    async def list_products(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        status: ProductStatus | None = None,
        q: str | None = None,
    ) -> tuple[list[Product], str | None]:
        statement = self._products_with_reserved().where(products.c.seller_id == seller_id)
        if status is not None:
            statement = statement.where(products.c.status == status)
        words = sorted(tokens(q or ""))
        if words:
            query = sa.func.to_tsquery("spanish", " | ".join(words))
            name = sa.func.f_unaccent(sa.func.lower(products.c.name))
            statement = statement.where(
                sa.or_(
                    products.c.search_tsv.op("@@")(query),
                    sa.func.word_similarity(name, " ".join(words)) > NAME_SIMILARITY,
                )
            )
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        keyset_page(statement, products, limit=limit, cursor=cursor)
                    )
                )
                .mappings()
                .all()
            )
        items, cursor_out = next_cursor(rows, limit=limit)
        return [_product_view(row) for row in items], cursor_out

    async def update_product(
        self, seller_id: UUID, product_id: UUID, data: ProductUpdate
    ) -> Product | None:
        changes = _sent(data.model_dump())
        owned = (products.c.id == product_id) & (products.c.seller_id == seller_id)
        async with self.sessions() as session, session.begin():
            current = (
                (await session.execute(sa.select(products).where(owned).with_for_update()))
                .mappings()
                .first()
            )
            if current is None:
                return None
            merged = {**current, **changes}
            if {"name", "description", "price_cop", "attributes"} & changes.keys():
                attributes = merged["attributes"] or {}
                changes["amounts_cop"] = list(
                    product_amounts(merged["price_cop"], merged["description"], attributes)
                )
                text = describe_product(
                    merged["name"], merged["description"], merged["price_cop"], attributes
                )
                changes["embedding"] = await self._embed(text)
            updated = (
                (
                    await session.execute(
                        sa.update(products).where(owned).values(changes).returning(*products.c)
                    )
                )
                .mappings()
                .one()
            )
            reserved = await session.scalar(sa.select(_reserved(product_id)))
        return _product_view({**updated, "reserved": reserved or 0})

    async def archive_product(self, seller_id: UUID, product_id: UUID) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                sa.update(products)
                .where(products.c.id == product_id, products.c.seller_id == seller_id)
                .values(status="archived")
                .returning(products.c.id)
            )
            return result.first() is not None

    async def create_fact(self, seller_id: UUID, data: FactCreate) -> Fact:
        values = _sent(data.model_dump())
        row = {
            "id": new_id(),
            "seller_id": seller_id,
            "kind": values["kind"],
            "text": values["text"],
            "source": values.get("source"),
            "status": "active",
            "amounts_cop": sorted(set(amounts_cop(values["text"]))),
            "embedding": await self._embed(values["text"]),
        }
        async with self.sessions() as session, session.begin():
            created = (
                (await session.execute(sa.insert(facts).values(row).returning(*facts.c)))
                .mappings()
                .one()
            )
        return _fact_view(created)

    async def list_facts(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        kind: FactKind | None = None,
    ) -> tuple[list[Fact], str | None]:
        statement = sa.select(facts).where(facts.c.seller_id == seller_id)
        if kind is not None:
            statement = statement.where(facts.c.kind == kind)
        async with self.sessions() as session:
            rows = (
                (await session.execute(keyset_page(statement, facts, limit=limit, cursor=cursor)))
                .mappings()
                .all()
            )
        items, cursor_out = next_cursor(rows, limit=limit)
        return [_fact_view(row) for row in items], cursor_out

    async def update_fact(self, seller_id: UUID, fact_id: UUID, data: FactUpdate) -> Fact | None:
        changes = _sent(data.model_dump())
        if "text" in changes:
            changes["amounts_cop"] = sorted(set(amounts_cop(changes["text"])))
            changes["embedding"] = await self._embed(changes["text"])
        owned = (facts.c.id == fact_id) & (facts.c.seller_id == seller_id)
        async with self.sessions() as session, session.begin():
            if not changes:
                row = (await session.execute(sa.select(facts).where(owned))).mappings().first()
            else:
                row = (
                    (
                        await session.execute(
                            sa.update(facts).where(owned).values(changes).returning(*facts.c)
                        )
                    )
                    .mappings()
                    .first()
                )
        return _fact_view(row) if row is not None else None

    async def delete_fact(self, seller_id: UUID, fact_id: UUID) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                sa.delete(facts)
                .where(facts.c.id == fact_id, facts.c.seller_id == seller_id)
                .returning(facts.c.id)
            )
            return result.first() is not None

    # ── Catalog protocol (agent tools) ──

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
        fact_kinds = sorted(wanted - {"product"}) if wanted else None
        include_products = not wanted or "product" in wanted
        words = sorted(tokens(query))
        vector = await self._embed(query) if words else None
        ranked: list[list[tuple[str, UUID]]] = []

        async with self.sessions() as session:

            async def collect(statement: sa.Select[Any], kind: str) -> None:
                ids = (await session.execute(statement.limit(CANDIDATES))).scalars().all()
                ranked.append([(kind, item) for item in ids])

            active_products = (products.c.seller_id == seller_id) & (products.c.status == "active")
            active_facts = (facts.c.seller_id == seller_id) & (facts.c.status == "active")
            if fact_kinds is not None:
                active_facts = active_facts & facts.c.kind.in_(fact_kinds)
            fact_kinds_wanted = fact_kinds is None or bool(fact_kinds)

            if words:
                query_ts = sa.func.to_tsquery("spanish", " | ".join(words))
                if include_products:
                    rank = sa.func.ts_rank(products.c.search_tsv, query_ts)
                    await collect(
                        sa.select(products.c.id)
                        .where(active_products, products.c.search_tsv.op("@@")(query_ts))
                        .order_by(rank.desc()),
                        "product",
                    )
                    similarity = sa.func.word_similarity(
                        sa.func.f_unaccent(sa.func.lower(products.c.name)), " ".join(words)
                    )
                    await collect(
                        sa.select(products.c.id)
                        .where(active_products, similarity > NAME_SIMILARITY)
                        .order_by(similarity.desc()),
                        "product",
                    )
                if fact_kinds_wanted:
                    rank = sa.func.ts_rank(facts.c.search_tsv, query_ts)
                    await collect(
                        sa.select(facts.c.id)
                        .where(active_facts, facts.c.search_tsv.op("@@")(query_ts))
                        .order_by(rank.desc()),
                        "fact",
                    )
                    # A question that names a kind ("horario") ranks that kind's facts too,
                    # whatever their text says (FACT_KIND_NAMES).
                    if named := named_kinds(query):
                        await collect(
                            sa.select(facts.c.id)
                            .where(active_facts, facts.c.kind.in_(named))
                            .order_by(facts.c.created_at.desc(), facts.c.id),
                            "fact",
                        )
            if vector is not None:
                if include_products:
                    await collect(
                        sa.select(products.c.id)
                        .where(active_products, products.c.embedding.isnot(None))
                        .order_by(products.c.embedding.cosine_distance(vector)),
                        "product",
                    )
                if fact_kinds_wanted:
                    await collect(
                        sa.select(facts.c.id)
                        .where(active_facts, facts.c.embedding.isnot(None))
                        .order_by(facts.c.embedding.cosine_distance(vector)),
                        "fact",
                    )
            if image_embedding is not None and include_products:
                await collect(
                    sa.select(products.c.id)
                    .where(active_products, products.c.image_embedding.isnot(None))
                    .order_by(products.c.image_embedding.cosine_distance(list(image_embedding))),
                    "product",
                )

            scores: dict[tuple[str, UUID], float] = defaultdict(float)
            for listing in ranked:
                for position, key in enumerate(listing, start=1):
                    scores[key] += 1 / (RRF_K + position)
            best = sorted(scores.items(), key=lambda item: -item[1])[:top_k]
            product_ids = [item for (kind, item), _ in best if kind == "product"]
            fact_ids = [item for (kind, item), _ in best if kind == "fact"]
            product_rows = {
                row["id"]: row
                for row in (
                    await session.execute(
                        self._products_with_reserved().where(products.c.id.in_(product_ids))
                    )
                ).mappings()
            }
            fact_rows = {
                row["id"]: row
                for row in (
                    await session.execute(sa.select(facts).where(facts.c.id.in_(fact_ids)))
                ).mappings()
            }

        results: list[SearchResult] = []
        for (kind, item), score in best:
            if kind == "product" and item in product_rows:
                row = product_rows[item]
                stock = row["stock"]
                results.append(
                    SearchResult(
                        id=item,
                        kind="product",
                        text=describe_product(
                            row["name"],
                            row["description"],
                            row["price_cop"],
                            row["attributes"] or {},
                        ),
                        price_cop=row["price_cop"],
                        stock=None if stock is None else max(stock - int(row["reserved"]), 0),
                        score=round(score, 6),
                    )
                )
            elif kind == "fact" and item in fact_rows:
                row = fact_rows[item]
                results.append(
                    SearchResult(id=item, kind=row["kind"], text=row["text"], score=round(score, 6))
                )
        return results

    async def _active(
        self, session: AsyncSession, seller_id: UUID, product_id: UUID, *, lock: bool = False
    ) -> Any:
        statement = self._products_with_reserved().where(
            products.c.id == product_id,
            products.c.seller_id == seller_id,
            products.c.status == "active",
        )
        if lock:
            statement = (
                sa.select(products)
                .where(
                    products.c.id == product_id,
                    products.c.seller_id == seller_id,
                    products.c.status == "active",
                )
                .with_for_update()
            )
        row = (await session.execute(statement)).mappings().first()
        if row is None:
            raise ProductNotFoundError(f"no active product {product_id} for this seller")
        return row

    async def get_product(self, seller_id: UUID, product_id: UUID) -> ProductDetail:
        async with self.sessions() as session:
            row = await self._active(session, seller_id, product_id)
        stock = row["stock"]
        return ProductDetail(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            price_cop=row["price_cop"],
            stock=stock,
            available=None if stock is None else max(stock - int(row["reserved"]), 0),
            attributes=row["attributes"] or {},
        )

    async def check_stock(
        self, seller_id: UUID, product_id: UUID, variant: Mapping[str, str] | None = None
    ) -> StockLevel:
        # Stock is per product in data-model.md; the variant only informs the reply.
        async with self.sessions() as session:
            row = await self._active(session, seller_id, product_id)
        stock = row["stock"]
        available = None if stock is None else max(stock - int(row["reserved"]), 0)
        return StockLevel(product_id=product_id, available=available, tracked=stock is not None)

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
        async with self.sessions() as session, session.begin():
            # The row lock serializes reservations of one product: no overselling (AS-3.7).
            product = await self._active(session, seller_id, product_id, lock=True)
            if product["stock"] is None:
                return Reservation(status="not_tracked")
            held = await session.scalar(sa.select(_reserved(product_id)))
            if quantity > product["stock"] - int(held or 0):
                return Reservation(status="insufficient_stock")
            while True:
                code = "R-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
                taken = await session.scalar(
                    sa.select(reservations.c.id).where(
                        reservations.c.seller_id == seller_id, reservations.c.code == code
                    )
                )
                if taken is None:
                    break
            created = (
                await session.execute(
                    sa.insert(reservations)
                    .values(
                        id=new_id(),
                        seller_id=seller_id,
                        conversation_id=conversation_id,
                        product_id=product_id,
                        quantity=quantity,
                        variant=dict(variant or {}),
                        code=code,
                        expires_at=sa.func.now() + RESERVATION_TTL,
                    )
                    .returning(reservations.c.id, reservations.c.code, reservations.c.expires_at)
                )
            ).one()
        return Reservation(
            reservation_id=created.id,
            code=created.code,
            expires_at=created.expires_at,
            status="reserved",
        )

    async def citations(self, seller_id: UUID, ids: Sequence[UUID]) -> list[Citation]:
        wanted = list(ids)
        if not wanted:
            return []
        async with self.sessions() as session:
            product_rows = (
                await session.execute(
                    sa.select(products.c.id, products.c.amounts_cop).where(
                        products.c.id.in_(wanted),
                        products.c.seller_id == seller_id,
                        products.c.status == "active",
                    )
                )
            ).all()
            fact_rows = (
                await session.execute(
                    sa.select(facts.c.id, facts.c.kind, facts.c.amounts_cop).where(
                        facts.c.id.in_(wanted),
                        facts.c.seller_id == seller_id,
                        facts.c.status == "active",
                    )
                )
            ).all()
        found: dict[UUID, Citation] = {
            row.id: Citation(id=row.id, kind="product", amounts_cop=tuple(sorted(row.amounts_cop)))
            for row in product_rows
        }
        found.update(
            {
                row.id: Citation(
                    id=row.id, kind=row.kind, amounts_cop=tuple(sorted(row.amounts_cop))
                )
                for row in fact_rows
            }
        )
        return [found[item] for item in wanted if item in found]
