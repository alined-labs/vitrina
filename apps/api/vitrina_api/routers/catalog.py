"""Catalog router. Owner: MX0 (plan.md §3).

Contract paths: /products, /products/import, /products/{product_id}, /facts, /facts/{fact_id}.
``/products/import`` arrives with T021.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from vitrina_catalog.api import (
    CatalogStore,
    Fact,
    FactCreate,
    FactKind,
    FactUpdate,
    Product,
    ProductCreate,
    ProductStatus,
    ProductUpdate,
)
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)

router = APIRouter(tags=["Catalog"])


def catalog_store(request: Request) -> CatalogStore:
    store: CatalogStore = request.app.state.catalog
    return store


Store = Annotated[CatalogStore, Depends(catalog_store)]
Paging = Annotated[PageQuery, Depends()]


def _not_found(kind: str, item_id: UUID) -> ProblemError:
    return ProblemError(ProblemCode.NOT_FOUND, detail=f"no {kind} {item_id} for this seller")


@router.get("/products", operation_id="listProducts", summary="List products")
async def list_products(
    seller_id: SellerId,
    store: Store,
    page: Paging,
    status: ProductStatus | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Semantic + keyword search")] = None,
) -> Page[Product]:
    items, cursor = await store.list_products(
        seller_id, limit=page.limit, cursor=page.cursor, status=status, q=q
    )
    return Page[Product](items=items, next_cursor=cursor)


@router.post(
    "/products",
    status_code=201,
    operation_id="createProduct",
    summary="Create a product",
    responses=problem_responses(400),
)
async def create_product(
    seller_id: SellerId, store: Store, product: ProductCreate, _key: IdempotencyKey = None
) -> Product:
    return await store.create_product(seller_id, product)


@router.get(
    "/products/{product_id}",
    operation_id="getProduct",
    summary="Get a product",
    responses=problem_responses(404),
)
async def get_product(seller_id: SellerId, store: Store, product_id: UUID) -> Product:
    product = await store.product(seller_id, product_id)
    if product is None:
        raise _not_found("product", product_id)
    return product


@router.patch(
    "/products/{product_id}",
    operation_id="updateProduct",
    summary="Update a product (approve a draft by setting status=active)",
    responses=problem_responses(404),
)
async def update_product(
    seller_id: SellerId, store: Store, product_id: UUID, changes: ProductUpdate
) -> Product:
    product = await store.update_product(seller_id, product_id, changes)
    if product is None:
        raise _not_found("product", product_id)
    return product


@router.delete(
    "/products/{product_id}",
    status_code=204,
    operation_id="archiveProduct",
    summary="Archive a product",
    responses=problem_responses(404),
)
async def archive_product(seller_id: SellerId, store: Store, product_id: UUID) -> None:
    if not await store.archive_product(seller_id, product_id):
        raise _not_found("product", product_id)


@router.get(
    "/facts", operation_id="listFacts", summary="List facts (FAQ, shipping, payment, policies)"
)
async def list_facts(
    seller_id: SellerId, store: Store, page: Paging, kind: FactKind | None = None
) -> Page[Fact]:
    items, cursor = await store.list_facts(
        seller_id, limit=page.limit, cursor=page.cursor, kind=kind
    )
    return Page[Fact](items=items, next_cursor=cursor)


@router.post("/facts", status_code=201, operation_id="createFact", summary="Create a fact")
async def create_fact(
    seller_id: SellerId, store: Store, fact: FactCreate, _key: IdempotencyKey = None
) -> Fact:
    return await store.create_fact(seller_id, fact)


@router.patch("/facts/{fact_id}", operation_id="updateFact", summary="Update or approve a fact")
async def update_fact(
    seller_id: SellerId, store: Store, fact_id: UUID, changes: FactUpdate
) -> Fact:
    fact = await store.update_fact(seller_id, fact_id, changes)
    if fact is None:
        raise _not_found("fact", fact_id)
    return fact


@router.delete(
    "/facts/{fact_id}", status_code=204, operation_id="deleteFact", summary="Delete a fact"
)
async def delete_fact(seller_id: SellerId, store: Store, fact_id: UUID) -> None:
    if not await store.delete_fact(seller_id, fact_id):
        raise _not_found("fact", fact_id)
