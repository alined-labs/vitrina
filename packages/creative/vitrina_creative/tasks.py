"""Creative worker task (T034), discovered by the worker through the ``vitrina.tasks`` entry point.

The job's payload is the snapshot the API took when the seller started it (product, active facts,
seller voice, photo keys, parameters); the task runs the pipeline, stores each scene in the blob
store and its row, and marks the job ready, or failed with a reason the seller can read.
"""

from typing import Any
from uuid import UUID

from vitrina_core.ids import new_id
from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry
from vitrina_core.providers import ProviderError
from vitrina_core.storage import BlobNotFoundError, BlobStore, blob_key, seller_of
from vitrina_creative.copy import BrandVoice
from vitrina_creative.jobs import CreativeJobStore
from vitrina_creative.pipeline import (
    PipelineInput,
    PipelineModels,
    ProductSnapshot,
    SellerSnapshot,
    run_pipeline,
)
from vitrina_creative.verifier import Thresholds

CREATIVE_TASK = "creative.run_job"


async def run_creative_job(ctx: JobContext) -> str:
    seller_id = ctx.owner_seller_id
    if seller_id is None or ctx.sessions is None or ctx.storage is None or ctx.providers is None:
        raise JobFailedError(
            "INTERNAL_ERROR", "creative jobs need the database, storage and providers"
        )
    store = CreativeJobStore(ctx.sessions)
    done = await store.get(seller_id, ctx.job_id)
    if done is not None and done["status"] == "succeeded":
        # A retry after success (the worker lost the result): rerunning would overwrite the
        # stored scenes that approved ads point at, so the first result stands.
        return f"/v1/creative/jobs/{ctx.job_id}"
    try:
        data = await load_input(ctx.payload, seller_id, ctx.storage)
        models = PipelineModels(
            segment=ctx.settings.model_id("segment"),
            image=ctx.settings.model_id("image"),
            chat=ctx.settings.model_id("agent"),
        )
    except BlobNotFoundError as error:
        raise await _fail(
            store, ctx, "NOT_FOUND", "Una de las fotos ya no está guardada."
        ) from error
    except LookupError as error:  # a model ID is not configured (Q01)
        raise await _fail(store, ctx, "PROVIDER_UNAVAILABLE", str(error)) from error

    async def on_stage(stage: str, fraction: float) -> None:
        await ctx.progress(stage, fraction)
        await store.set_stage(ctx.job_id, stage, fraction)

    try:
        result = await run_pipeline(
            data,
            segmenter=ctx.providers.segmenter,
            image_model=ctx.providers.image,
            chat=ctx.providers.chat,
            models=models,
            thresholds=Thresholds.from_settings(ctx.settings),
            on_stage=on_stage,
        )
    except JobFailedError as error:
        raise await _fail(store, ctx, error.code, error.message) from error
    except Exception as error:
        if ctx.job.attempt >= ctx.job.max_attempts:  # the worker gives up after this attempt
            code = "PROVIDER_ERROR" if isinstance(error, ProviderError) else "INTERNAL_ERROR"
            await store.fail(ctx.job_id, code, str(error)[:300])
        raise

    product_id = UUID(str(ctx.payload["params"]["product_id"]))
    scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(result.scenes):
        key = blob_key(seller_id, "creatives", f"{ctx.job_id}/{index:02d}-{scene.format}", "png")
        await ctx.storage.put(key, scene.image.data, "image/png")
        scenes.append(
            {
                "id": new_id(),
                "job_id": ctx.job_id,
                "seller_id": seller_id,
                "product_id": product_id,
                "format": scene.format,
                "storage_key": key,
                "mask_key": None,
                "source_asset_id": scene.source_asset_id,
                "scene_prompt": scene.scene_prompt,
                "verification": scene.verification.model_dump(mode="json"),
                "provenance": {
                    "generator": models.image,
                    "segmenter": models.segment,
                    "ai_generated": True,
                    "label_applied": False,
                },
            }
        )
    copy = [
        {
            "id": new_id(),
            "job_id": ctx.job_id,
            "seller_id": seller_id,
            "hook": variant.hook,
            "angle": variant.angle,
            "text": variant.text,
            "brand_voice_score": (
                round(variant.brand_voice_score, 1)
                if variant.brand_voice_score is not None
                else None
            ),
            "brand_voice_judge": variant.brand_voice_judge,
            "status": variant.status,
        }
        for variant in result.copy
    ]
    await store.finish(ctx.job_id, scenes, copy)
    return f"/v1/creative/jobs/{ctx.job_id}"


async def load_input(payload: Any, seller_id: UUID, storage: BlobStore) -> PipelineInput:
    """The snapshot the API took, with each photo read back under the seller's own keys."""
    params, product, seller = payload["params"], payload["product"], payload["seller"]
    photos = []
    for entry in payload["photos"]:
        key = str(entry["key"])
        if seller_of(key) != seller_id:  # Art. V.3
            raise JobFailedError("VALIDATION_ERROR", "a photo belongs to another seller")
        photos.append((UUID(str(entry["asset_id"])), await storage.get(key)))
    return PipelineInput(
        product=ProductSnapshot(
            name=product["name"],
            price_cop=int(product["price_cop"]),
            description=product.get("description") or "",
            stock=product.get("stock"),
            attributes=dict(product.get("attributes") or {}),
        ),
        seller=SellerSnapshot(
            business_name=seller["business_name"],
            city=seller.get("city") or "",
            voice=BrandVoice(**(seller.get("voice") or {})),
            brand_voice_min=float(seller.get("brand_voice_min", 3.5)),
            ai_label_enabled=bool(seller.get("ai_label_enabled", True)),
        ),
        photos=tuple(photos),
        facts=tuple(payload.get("facts") or ()),
        formats=tuple(params.get("formats") or ("story_9x16", "catalog_1x1")),
        scenes_per_format=int(params.get("scenes_per_format", 3)),
        copy_variants=int(params.get("copy_variants", 10)),
        angles=tuple(params.get("angles") or ()),
        scene_brief=params.get("scene_brief"),
    )


async def _fail(
    store: CreativeJobStore, ctx: JobContext, code: str, message: str
) -> JobFailedError:
    await store.fail(ctx.job_id, code, message)
    return JobFailedError(code, message)


def register(registry: TaskRegistry) -> None:
    registry.add(CREATIVE_TASK, run_creative_job, timeout_s=900)
