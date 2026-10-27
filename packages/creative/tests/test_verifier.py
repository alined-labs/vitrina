"""T033 fidelity verifier: three signals, thresholds from settings, a fail-closed review (FR-012)."""

import json
from datetime import UTC, datetime

import pytest

from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    Segment,
    SegmentRequest,
    SegmentResponse,
)
from vitrina_core.settings import Settings
from vitrina_creative.api import SourceProduct, Thresholds, VerifierModels, verify_scene
from vitrina_creative.imaging import (
    aligned_iou,
    color_delta,
    decode_mask,
    decode_rgb,
    encode_png,
    mask_bbox,
)
from vitrina_creative.testing import (
    SyntheticScene,
    altered_scenes,
    good_scenes,
    logo_removed_scene,
    source_photo,
)
from vitrina_creative.verifier import INCONCLUSIVE, REVIEW_SCHEMA, parse_review

MODELS = VerifierModels(segment="segment-model-from-settings", review="agent-model-from-settings")
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
UNCHANGED = json.dumps({"altered": False, "reason": "sin cambios en el producto"})


class MaskOracle:
    """A perfect segmenter: the true product mask of each known scene, nothing otherwise."""

    def __init__(self, *scenes: SyntheticScene) -> None:
        self.masks = {scene.image.sha256: scene.mask for scene in scenes}
        self.requests: list[SegmentRequest] = []

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        self.requests.append(request)
        mask = self.masks.get(request.image.sha256)
        box = None if mask is None else mask_bbox(decode_mask(mask))
        segments = (
            () if mask is None or box is None else (Segment(mask=mask, bbox=box, score=0.97),)
        )
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return SegmentResponse(segments=segments, meta=meta)


class ScriptedReviewer:
    """Answers with the given texts in order, repeating the last one."""

    def __init__(self, *answers: str) -> None:
        self.answers = answers or (UNCHANGED,)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        text = self.answers[min(len(self.requests), len(self.answers)) - 1]
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return ChatResponse(message=ChatMessage(role="assistant", content=text), meta=meta)


def source() -> SourceProduct:
    photo = source_photo()
    return SourceProduct(image=photo.image, mask=photo.mask, label="bolso", possessive="del bolso")


async def check(scene: SyntheticScene, reviewer: ScriptedReviewer | None = None, **kwargs: object):
    return await verify_scene(
        source(),
        scene.image,
        segmenter=MaskOracle(scene),
        reviewer=reviewer or ScriptedReviewer(),
        models=MODELS,
        thresholds=kwargs.pop("thresholds", Thresholds()),  # type: ignore[arg-type]
        now=NOW,
    )


async def test_as_2_2_report_carries_three_signals_thresholds_and_a_spanish_reason() -> None:
    report = await check(good_scenes()[0])
    assert set(report.model_dump()) == {
        "status", "mask_iou", "color_delta", "visual_review", "thresholds", "reason", "checked_at",
    }  # fmt: skip
    assert report.status == "passed"
    assert report.mask_iou >= 0.85
    assert report.color_delta <= 0.25
    assert report.visual_review.altered is False
    assert report.thresholds == Thresholds(mask_iou_min=0.85, color_delta_max=0.25)
    assert report.reason == "producto sin cambios — aprobado"
    assert report.checked_at == NOW


@pytest.mark.parametrize("scene", good_scenes(), ids=lambda scene: scene.name)
async def test_good_scenes_pass(scene: SyntheticScene) -> None:
    report = await check(scene)
    assert report.status == "passed", report


@pytest.mark.parametrize("scene", altered_scenes(), ids=lambda scene: scene.name)
async def test_as_2_3_altered_scenes_are_blocked_even_when_the_review_sees_nothing(
    scene: SyntheticScene,
) -> None:
    report = await check(scene, ScriptedReviewer(UNCHANGED))
    assert report.status == "blocked", report
    assert report.mask_iou < 0.85 or report.color_delta > 0.25
    assert report.reason.endswith("— bloqueado")


async def test_as_2_3_a_colour_change_names_the_product_in_spanish() -> None:
    blue = next(scene for scene in altered_scenes() if scene.name == "blue")
    report = await check(blue)
    assert report.reason == "color del bolso alterado — bloqueado"


async def test_as_2_3_the_visual_review_blocks_what_the_metrics_miss() -> None:
    scene = logo_removed_scene()
    metrics_only = await check(scene)
    assert metrics_only.status == "passed"  # same shape, a few white pixels less: metrics agree
    reviewer = ScriptedReviewer(
        json.dumps({"altered": True, "reason": "logotipo del bolso eliminado"})
    )
    report = await check(scene, reviewer)
    assert report.status == "blocked"
    assert report.visual_review.altered is True
    assert report.reason == "logotipo del bolso eliminado — bloqueado"


async def test_a_scene_without_the_product_is_blocked() -> None:
    lost = SyntheticScene(name="lost", image=good_scenes()[0].image, mask=good_scenes()[0].mask)
    report = await verify_scene(
        source(),
        lost.image,
        segmenter=MaskOracle(),  # knows no scene: finds nothing
        reviewer=ScriptedReviewer(),
        models=MODELS,
        thresholds=Thresholds(),
        now=NOW,
    )
    assert report.status == "blocked"
    assert (report.mask_iou, report.color_delta) == (0.0, 1.0)
    assert report.reason == "el producto no aparece en la escena — bloqueado"


async def test_a_malformed_review_is_retried_once_then_fails_closed() -> None:
    reviewer = ScriptedReviewer("no estoy seguro", "tampoco")
    report = await check(good_scenes()[1], reviewer)
    assert len(reviewer.requests) == 2
    assert report.visual_review == INCONCLUSIVE
    assert report.status == "blocked"


async def test_one_malformed_review_then_a_valid_one_is_used() -> None:
    reviewer = ScriptedReviewer("¿qué?", UNCHANGED)
    report = await check(good_scenes()[1], reviewer)
    assert len(reviewer.requests) == 2
    assert report.status == "passed"


async def test_thresholds_come_from_settings_and_are_stamped_on_the_report() -> None:
    scene = good_scenes()[1]  # passes the defaults with a mask_iou of about 0.994
    assert (await check(scene)).status == "passed"
    thresholds = Thresholds.from_settings(Settings(mask_iou_min=0.999, color_delta_max=0.25))
    report = await check(scene, thresholds=thresholds)
    assert report.thresholds == Thresholds(mask_iou_min=0.999, color_delta_max=0.25)
    assert report.status == "blocked"  # the same scene fails a stricter setting


def test_model_ids_come_from_settings_and_are_never_guessed() -> None:
    with pytest.raises(LookupError):
        VerifierModels.from_settings(Settings(model_segment=None, model_agent=None))
    models = VerifierModels.from_settings(Settings(model_segment="seg-id", model_agent="spark-id"))
    assert models == VerifierModels(segment="seg-id", review="spark-id")


async def test_the_review_is_a_spark_vision_call_with_the_contract_schema() -> None:
    reviewer = ScriptedReviewer()
    segmenter = MaskOracle(good_scenes()[0])
    await verify_scene(
        source(), good_scenes()[0].image, segmenter=segmenter, reviewer=reviewer, models=MODELS,
        thresholds=Thresholds(), now=NOW,
    )  # fmt: skip
    (request,) = reviewer.requests
    assert request.model == "agent-model-from-settings"
    assert request.purpose == "verify"
    assert request.temperature == 0.0
    assert request.response_format is not None
    assert request.response_format.json_schema == REVIEW_SCHEMA
    user = request.messages[1].content
    assert isinstance(user, tuple)
    assert sum(isinstance(part, ImagePart) for part in user) == 2
    (segment_request,) = segmenter.requests
    assert (segment_request.model, segment_request.purpose, segment_request.prompt) == (
        "segment-model-from-settings", "verify", "bolso",
    )  # fmt: skip


def test_parse_review_tolerates_fences_and_trims_long_reasons() -> None:
    fenced = '```json\n{"altered": false, "reason": "sin cambios en el producto"}\n```'
    assert parse_review(fenced) == parse_review(UNCHANGED)
    long_reason = json.dumps({"altered": True, "reason": "x" * 300})
    review = parse_review(long_reason)
    assert review is not None
    assert len(review.reason) == 120
    assert parse_review('{"altered": "yes", "reason": "?"}') is None
    assert parse_review(None) is None


def test_metrics_are_exact_on_identical_products() -> None:
    photo = source_photo()
    mask, pixels = decode_mask(photo.mask), decode_rgb(photo.image)
    assert aligned_iou(mask, mask) == 1.0
    assert color_delta(pixels, mask, pixels, mask) == pytest.approx(0.0, abs=1e-9)
    empty = decode_mask(encode_png(mask & False))
    assert aligned_iou(mask, empty) == 0.0
