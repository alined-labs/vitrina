"""Catalog router. Owner: MX0 (plan.md §3).

Contract paths: /products, /products/import, /products/{product_id}, /facts, /facts/{fact_id}.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

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
from vitrina_catalog.importer import IMPORT_TASK, photo_type
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    FieldError,
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.ids import new_id
from vitrina_core.jobs import Job, PostgresJobQueue
from vitrina_core.storage import blob_key

router = APIRouter(tags=["Catalog"])

MAX_PHOTO_BYTES = 10 * 1024 * 1024  # FR-010: images up to 10 MB
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


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


@router.post(
    "/products/import",
    status_code=202,
    operation_id="importProductsFromImage",
    summary="Extract draft products from a photo of a menu or price list",
    description=(
        "Returns a Job whose result is a list of `draft` products that must be approved (AS-1.3)."
    ),
    responses=problem_responses(400),
)
async def import_products(
    request: Request,
    seller_id: SellerId,
    image: Annotated[UploadFile, File()],
    hint: Annotated[
        str | None, Form(max_length=300, description='Optional context, e.g. "menú de almuerzos"')
    ] = None,
    _key: IdempotencyKey = None,
) -> Job:
    photo = await image.read(MAX_PHOTO_BYTES + 1)
    mime_type = photo_type(photo)
    if len(photo) > MAX_PHOTO_BYTES or mime_type is None:
        message = (
            "the photo is larger than 10 MB"
            if len(photo) > MAX_PHOTO_BYTES
            else "send a JPEG, PNG or WebP photo"
        )
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR, errors=[FieldError(field="image", message=message)]
        )
    jobs: PostgresJobQueue | None = request.app.state.jobs
    if jobs is None or request.app.state.sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE,
            detail="product import needs the database (DATABASE_URL)",
        )
    # Stored before the job exists, so the worker never sees a job without its photo.
    key = blob_key(seller_id, "imports", new_id(), EXTENSIONS[mime_type])
    await request.app.state.storage.put(key, photo, mime_type)
    payload = {"image_key": key, **({"hint": hint} if hint else {})}
    async with request.app.state.sessions() as session, session.begin():
        job_id = await jobs.enqueue(
            session,
            kind="product_import",
            task=IMPORT_TASK,
            payload=payload,
            owner_seller_id=seller_id,
        )
    job = await jobs.get_for_seller(seller_id, job_id)
    assert job is not None
    return job


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
