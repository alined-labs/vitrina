"""Starting a creative job, for the route and for ``learning.apply_brief`` (T034; I-13, T071).

``job_payload`` snapshots what the pipeline needs through public interfaces: the product and
the seller's active facts and voice from ``catalog.api``, the product's photos from the asset
store. ``start_job`` then queues the job and its ``creative_jobs`` row in one transaction, so
the worker never reads another module's tables. A brief passes ``brief_id`` in the params.
The errors are the route's: 404 for an unknown product or seller, 400 on ``asset_ids``.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import CatalogStore, Product, SellerStore
from vitrina_core.http import FieldError, ProblemCode, ProblemError
from vitrina_core.jobs import PostgresJobQueue
from vitrina_creative.assets import AssetStore
from vitrina_creative.jobs import CreativeJobStore
from vitrina_creative.schemas import CreativeJobCreate
from vitrina_creative.tasks import CREATIVE_TASK

MAX_FACTS = 50
VOICE_KEYS = ("formality", "warmth", "emoji_rate", "regionalisms")
PRODUCT_KEYS = ("name", "description", "price_cop", "stock", "attributes")


async def start_job(
    seller_id: UUID,
    params: CreativeJobCreate,
    *,
    catalog: CatalogStore,
    sellers: SellerStore,
    assets: AssetStore,
    queue: PostgresJobQueue,
    sessions: async_sessionmaker[AsyncSession],
) -> UUID:
    """Queue a creative job for one of the seller's products and return its id.

    From a route: ``start_job(seller_id, params, catalog=state.catalog, sellers=state.sellers,
    assets=assets_from_state(state), queue=state.jobs, sessions=state.sessions)``.
    """
    payload = await job_payload(seller_id, params, catalog=catalog, sellers=sellers, assets=assets)
    async with sessions() as session, session.begin():
        job_id = await queue.enqueue(
            session, kind="creative", task=CREATIVE_TASK, payload=payload, owner_seller_id=seller_id
        )
        await CreativeJobStore.create(session, job_id=job_id, seller_id=seller_id, params=params)
    return job_id


async def job_payload(
    seller_id: UUID,
    params: CreativeJobCreate,
    *,
    catalog: CatalogStore,
    sellers: SellerStore,
    assets: AssetStore,
) -> dict[str, Any]:
    """What the worker needs, read now: later edits to the product do not change this job."""
    product = await catalog.product(seller_id, params.product_id)
    if product is None:
        raise ProblemError(
            ProblemCode.NOT_FOUND, detail=f"no product {params.product_id} for this seller"
        )
    return {
        "params": params.model_dump(mode="json"),
        "product": _product(product),
        "facts": await _facts(catalog, seller_id),
        "seller": await _seller(sellers, seller_id),
        "photos": await _photos(assets, seller_id, params),
    }


def _product(product: Product) -> dict[str, Any]:
    data = product.model_dump(mode="json")
    return {key: data[key] for key in PRODUCT_KEYS if key in data}


async def _facts(catalog: CatalogStore, seller_id: UUID) -> list[str]:
    facts, _cursor = await catalog.list_facts(seller_id, limit=MAX_FACTS)
    return [fact.text for fact in facts if fact.status == "active"]


async def _seller(sellers: SellerStore, seller_id: UUID) -> dict[str, Any]:
    seller = await sellers.get(seller_id)
    settings = await sellers.settings(seller_id)
    if seller is None or settings is None:
        raise ProblemError(ProblemCode.NOT_FOUND, detail="sign up with POST /sellers first")
    profile = settings.brand_voice_profile.model_dump(mode="json")
    return {
        "business_name": seller.business_name,
        "city": seller.city,
        "voice": {key: profile[key] for key in VOICE_KEYS if key in profile},
        "brand_voice_min": settings.brand_voice_min,
        "ai_label_enabled": settings.ai_label_enabled,
    }


async def _photos(
    assets: AssetStore, seller_id: UUID, params: CreativeJobCreate
) -> list[dict[str, str]]:
    images = [
        record
        for record in await assets.for_product(seller_id, params.product_id)
        if record.kind == "image"
    ]
    if isinstance(params.asset_ids, list):
        wanted = set(params.asset_ids)
        images = [record for record in images if record.id in wanted]
        if len(images) != len(wanted):
            raise ProblemError(
                ProblemCode.VALIDATION_ERROR,
                errors=[
                    FieldError(
                        field="asset_ids", message="incluye fotos que no son de este producto"
                    )
                ],
            )
    if not images:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="Sube al menos una foto del producto.",
            errors=[FieldError(field="asset_ids", message="el producto no tiene fotos")],
        )
    return [{"asset_id": str(record.id), "key": record.storage_key} for record in images]
