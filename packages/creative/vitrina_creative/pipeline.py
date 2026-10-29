"""The creative pipeline (T034, plan.md §4.2): segment → compose → verify → copywrite → score.

``run_pipeline`` is pure orchestration over a frozen snapshot of the job's inputs (product, facts,
seller voice, photos), taken by the API when the seller starts the job, so the worker needs no
other module's store (Art. VI.3) and a replayed job sees exactly the same prompts. Persistence is
the task's (``tasks.py``).
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from vitrina_core.providers import Blob, ChatModel, ImageModel, Segmenter
from vitrina_creative.compose import CreativeFormat, compose_scenes
from vitrina_creative.concept import sam_concept
from vitrina_creative.copy import (
    BrandVoice,
    CopyAngle,
    CopyHook,
    CopyInput,
    DroppedCell,
    write_copy,
)
from vitrina_creative.judge import RubricJudge, VoicedVariant, judge_copy
from vitrina_creative.provenance import Provenance, embed_provenance
from vitrina_creative.segment import SegmentationError, SegmentedPhoto, segment_photo
from vitrina_creative.verifier import Thresholds, Verification, VerifierModels, verify_scene

OnStage = Callable[[str, float], Awaitable[None]]


@dataclass(frozen=True)
class ProductSnapshot:
    name: str
    price_cop: int | None
    description: str = ""
    stock: int | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SellerSnapshot:
    business_name: str
    city: str
    voice: BrandVoice = field(default_factory=BrandVoice)
    brand_voice_min: float = 3.5
    ai_label_enabled: bool = True


@dataclass(frozen=True)
class PipelineInput:
    product: ProductSnapshot
    seller: SellerSnapshot
    photos: tuple[tuple[UUID, Blob], ...]
    facts: tuple[str, ...] = ()
    formats: tuple[CreativeFormat, ...] = ("story_9x16", "catalog_1x1")
    scenes_per_format: int = 3
    copy_variants: int = 10
    angles: tuple[CopyAngle, ...] = ()
    hooks: tuple[CopyHook, ...] = ()
    scene_brief: str | None = None


@dataclass(frozen=True)
class PipelineModels:
    """Model IDs from settings: SAM, Muse Image, and Spark for review, copy and judging."""

    segment: str
    image: str
    chat: str


@dataclass(frozen=True)
class SceneResult:
    format: CreativeFormat
    image: Blob  # PNG with provenance metadata
    scene_prompt: str
    source_asset_id: UUID
    verification: Verification


@dataclass(frozen=True)
class PipelineResult:
    scenes: list[SceneResult]
    copy: list[VoicedVariant]
    dropped_cells: tuple[DroppedCell, ...] = ()


async def run_pipeline(
    data: PipelineInput,
    *,
    segmenter: Segmenter,
    image_model: ImageModel,
    chat: ChatModel,
    models: PipelineModels,
    thresholds: Thresholds,
    on_stage: OnStage,
) -> PipelineResult:
    await on_stage("segmenting", 0.05)
    # SAM finds nothing for Spanish names, so it gets an English noun (concept.py).
    concept = await sam_concept(
        data.product.name, data.product.description, chat=chat, model=models.chat
    )
    source_id, source, photo = await _best_photo(
        data, concept, segmenter=segmenter, model=models.segment
    )

    await on_stage("composing", 0.2)
    scenes = await compose_scenes(
        source.cutout,
        product_name=data.product.name,
        formats=data.formats,
        scenes_per_format=data.scenes_per_format,
        scene_brief=data.scene_brief,
        image_model=image_model,
        model=models.image,
    )

    await on_stage("verifying", 0.5)
    verifier_models = VerifierModels(segment=models.segment, review=models.chat)
    photo_sha = hashlib.sha256(photo.data).hexdigest()
    results: list[SceneResult] = []
    for scene in scenes:
        verification = await verify_scene(
            source.product,
            scene.image,
            segmenter=segmenter,
            reviewer=chat,
            models=verifier_models,
            thresholds=thresholds,
        )
        provenance = Provenance(
            generator=models.image,
            source_sha256=photo_sha,
            verification=verification.status,
            created_at=datetime.now(UTC),
        )
        results.append(
            SceneResult(
                format=scene.format,
                image=embed_provenance(scene.image, provenance),
                scene_prompt=scene.scene_prompt,
                source_asset_id=source_id,
                verification=verification,
            )
        )

    if not any(result.verification.status == "passed" for result in results):
        return PipelineResult(scenes=results, copy=[])  # nothing approvable: no copy to write

    await on_stage("copywriting", 0.75)
    item = CopyInput(
        business_name=data.seller.business_name,
        city=data.seller.city,
        product_name=data.product.name,
        product_description=data.product.description,
        price_cop=data.product.price_cop,
        stock=data.product.stock,
        attributes=dict(data.product.attributes),
        facts=data.facts,
        voice=data.seller.voice,
        scene_brief=data.scene_brief,
    )
    written = await write_copy(
        item,
        count=data.copy_variants,
        angles=data.angles or None,
        hooks=data.hooks or None,
        writer=chat,
        model=models.chat,
    )

    await on_stage("scoring", 0.9)
    voiced = await judge_copy(
        item,
        written.variants,
        judge=RubricJudge(chat=chat, model=models.chat),
        writer=chat,
        writer_model=models.chat,
        minimum=data.seller.brand_voice_min,
    )
    return PipelineResult(scenes=results, copy=voiced, dropped_cells=written.dropped)


async def _best_photo(
    data: PipelineInput, concept: str, *, segmenter: Segmenter, model: str
) -> tuple[UUID, SegmentedPhoto, Blob]:
    """Segment every photo; compose from the one where the product is largest."""
    found: list[tuple[UUID, SegmentedPhoto, Blob]] = []
    last_error: SegmentationError | None = None
    for asset_id, photo in data.photos:
        try:
            segmented = await segment_photo(photo, label=concept, segmenter=segmenter, model=model)
        except SegmentationError as error:
            last_error = error
            continue
        found.append((asset_id, segmented, photo))
    if not found:
        raise last_error or SegmentationError(
            "PRODUCT_NOT_FOUND", "No encontramos fotos del producto para crear anuncios."
        )
    return max(found, key=lambda entry: entry[1].area)
