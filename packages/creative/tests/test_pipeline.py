"""T034 pipeline: stages in order, the altered scene blocked, copy only when a scene passed."""

from dataclasses import replace
from uuid import UUID

import pytest

from vitrina_core.providers import Blob
from vitrina_creative.pipeline import (
    PipelineInput,
    PipelineModels,
    ProductSnapshot,
    SellerSnapshot,
    run_pipeline,
)
from vitrina_creative.provenance import read_provenance
from vitrina_creative.segment import SegmentationError
from vitrina_creative.testing import (
    MaskOracle,
    ScriptedMuse,
    SparkByPurpose,
    altered_scenes,
    backdrop,
    bag,
    good_scenes,
    place,
    source_photo,
)
from vitrina_creative.verifier import Thresholds

SOURCE_ID = UUID("00000000-0000-7000-8000-0000000000a1")
TINY_ID = UUID("00000000-0000-7000-8000-0000000000a2")
MODELS = PipelineModels(
    segment="sam-from-settings", image="muse-from-settings", chat="spark-from-settings"
)
TINY = place("tiny", backdrop("beige"), bag(scale=0.2), (256, 256))
BLUE = next(scene for scene in altered_scenes() if scene.name == "blue")


def job(*photos: tuple[UUID, Blob], scenes_per_format: int = 3) -> PipelineInput:
    return PipelineInput(
        product=ProductSnapshot(
            name="Bolso tejido a mano",
            price_cop=85_000,
            stock=3,
            description="Hilo de algodón, tejido a mano.",
        ),
        seller=SellerSnapshot(business_name="Tejidos Vecina", city="Medellín"),
        photos=photos or ((SOURCE_ID, source_photo().image),),
        facts=("Envío a Medellín $12.000, 2–3 días",),
        formats=("catalog_1x1",),
        scenes_per_format=scenes_per_format,
        copy_variants=6,
    )


async def run(data: PipelineInput, muse: ScriptedMuse, stages: list[str] | None = None):
    async def on_stage(stage: str, fraction: float) -> None:
        if stages is not None:
            stages.append(stage)

    oracle = MaskOracle(source_photo(), TINY, *good_scenes(), *altered_scenes())
    return await run_pipeline(
        data, segmenter=oracle, image_model=muse, chat=SparkByPurpose(), models=MODELS,
        thresholds=Thresholds(), on_stage=on_stage,
    )  # fmt: skip


async def test_as_2_1_the_job_walks_the_stages_and_blocks_the_altered_scene() -> None:
    stages: list[str] = []
    result = await run(job(), ScriptedMuse(good_scenes()[0], BLUE, good_scenes()[1]), stages)
    assert stages == ["segmenting", "composing", "verifying", "copywriting", "scoring"]
    assert [scene.verification.status for scene in result.scenes] == ["passed", "blocked", "passed"]
    assert result.scenes[1].verification.reason == "color del producto alterado — bloqueado"
    assert {scene.source_asset_id for scene in result.scenes} == {SOURCE_ID}
    assert read_provenance(result.scenes[1].image)["verification"] == "blocked"
    assert len(result.copy) == 6
    assert all(variant.brand_voice_score == 4.0 for variant in result.copy)


async def test_as_2_3_no_passed_scene_means_no_copy_is_written() -> None:
    stages: list[str] = []
    result = await run(job(scenes_per_format=2), ScriptedMuse(BLUE), stages)
    assert [scene.verification.status for scene in result.scenes] == ["blocked", "blocked"]
    assert result.copy == []
    assert "copywriting" not in stages


async def test_a_photo_with_a_tiny_product_is_skipped_for_a_usable_one() -> None:
    result = await run(
        job((TINY_ID, TINY.image), (SOURCE_ID, source_photo().image)),
        ScriptedMuse(good_scenes()[0]),
    )
    assert {scene.source_asset_id for scene in result.scenes} == {SOURCE_ID}


async def test_a_jobs_hooks_reach_the_copy() -> None:
    result = await run(replace(job(), hooks=("question",)), ScriptedMuse(good_scenes()[0]))
    assert result.copy
    assert {variant.hook for variant in result.copy} == {"question"}


async def test_no_usable_photo_fails_the_job_with_a_reason_for_the_seller() -> None:
    with pytest.raises(SegmentationError) as caught:
        await run(job((TINY_ID, TINY.image)), ScriptedMuse(good_scenes()[0]))
    assert caught.value.code == "PRODUCT_TOO_SMALL"
