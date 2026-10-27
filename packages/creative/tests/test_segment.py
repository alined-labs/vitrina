"""T031 segment stage: mask, box, area check and cut-out from SAM, replayable from cassettes."""

import json
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vitrina_core.jobs import JobFailedError
from vitrina_core.providers import (
    Blob,
    CallMeta,
    CassetteMode,
    Cassettes,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    RecordedSegmenter,
    Segment,
    SegmentRequest,
    SegmentResponse,
)
from vitrina_creative.api import (
    SegmentationError,
    Thresholds,
    VerifierModels,
    segment_photo,
    verify_scene,
)
from vitrina_creative.imaging import decode_mask, encode_png, mask_bbox
from vitrina_creative.testing import backdrop, bag, good_scenes, place, source_photo

MODEL = "segment-model-from-settings"


class FakeSam:
    """Returns the given (mask, score) segments for every photo and records the requests."""

    def __init__(self, *segments: tuple[Blob, float]) -> None:
        self.segments = segments
        self.requests: list[SegmentRequest] = []

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        self.requests.append(request)
        found = []
        for mask, score in self.segments:
            box = mask_bbox(decode_mask(mask))
            if box is not None:
                found.append(Segment(mask=mask, bbox=box, score=score))
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return SegmentResponse(segments=tuple(found), meta=meta)


def cassette_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.json"))


async def run(photo: Blob, sam: FakeSam | RecordedSegmenter):
    return await segment_photo(
        photo, label="bolso", possessive="del bolso", segmenter=sam, model=MODEL
    )


async def test_segment_returns_mask_box_area_and_a_transparent_cutout() -> None:
    photo = source_photo()
    sam = FakeSam((photo.mask, 0.95))
    result = await run(photo.image, sam)
    mask = decode_mask(photo.mask)
    x0, y0, x1, y1 = mask_bbox(mask) or (0, 0, 0, 0)
    assert result.bbox == (x0, y0, x1, y1)
    assert result.area == pytest.approx(float(mask.mean()), abs=1e-4)
    cut = Image.open(BytesIO(result.cutout.data))
    assert (cut.mode, cut.size) == ("RGBA", (x1 - x0, y1 - y0))
    assert ((np.asarray(cut)[..., 3] > 0) == mask[y0:y1, x0:x1]).all()
    assert (result.product.label, result.product.possessive) == ("bolso", "del bolso")
    (request,) = sam.requests
    assert (request.model, request.purpose, request.prompt) == (MODEL, "segment", "bolso")


async def test_the_highest_scoring_segment_is_the_product() -> None:
    photo = source_photo()
    everything = encode_png(np.ones((512, 512), dtype=np.bool_))
    result = await run(photo.image, FakeSam((everything, 0.40), (photo.mask, 0.93)))
    assert result.bbox == mask_bbox(decode_mask(photo.mask))


async def test_a_product_under_5_percent_of_the_photo_is_rejected() -> None:
    tiny = place("tiny", backdrop("beige"), bag(scale=0.2), (256, 256))
    with pytest.raises(SegmentationError) as caught:
        await run(tiny.image, FakeSam((tiny.mask, 0.9)))
    assert caught.value.code == "PRODUCT_TOO_SMALL"
    assert "foto más cercana" in caught.value.message
    assert isinstance(caught.value, JobFailedError)  # a final failure: the worker does not retry


async def test_no_segment_means_the_product_was_not_found() -> None:
    with pytest.raises(SegmentationError) as caught:
        await run(source_photo().image, FakeSam())
    assert caught.value.code == "PRODUCT_NOT_FOUND"


async def test_a_mask_of_another_size_is_fitted_to_the_photo() -> None:
    photo = source_photo()
    half = Image.open(BytesIO(photo.mask.data)).resize((256, 256), Image.Resampling.NEAREST)
    small_mask = encode_png(np.asarray(half.convert("L"), dtype=np.uint8) > 127)
    result = await run(photo.image, FakeSam((small_mask, 0.9)))
    expected = mask_bbox(decode_mask(photo.mask)) or (0, 0, 0, 0)
    assert all(abs(a - b) <= 2 for a, b in zip(result.bbox, expected, strict=True))


async def test_t031_replays_from_a_cassette_without_the_live_provider(tmp_path: Path) -> None:
    photo = source_photo()
    recorder = RecordedSegmenter(
        Cassettes(tmp_path, CassetteMode.record), FakeSam((photo.mask, 0.95))
    )
    recorded = await run(photo.image, recorder)
    assert len(cassette_files(tmp_path)) == 1
    replayed = await run(photo.image, RecordedSegmenter(Cassettes(tmp_path, CassetteMode.replay)))
    assert (replayed.bbox, replayed.area, replayed.cutout) == (
        recorded.bbox,
        recorded.area,
        recorded.cutout,
    )


async def test_the_segmented_photo_is_the_verifiers_source() -> None:
    photo, scene = source_photo(), good_scenes()[0]
    segmented = await run(photo.image, FakeSam((photo.mask, 0.95)))

    class Unchanged:
        async def complete(self, request: ChatRequest) -> ChatResponse:
            text = json.dumps({"altered": False, "reason": "sin cambios en el producto"})
            meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
            return ChatResponse(message=ChatMessage(role="assistant", content=text), meta=meta)

    report = await verify_scene(
        segmented.product,
        scene.image,
        segmenter=FakeSam((scene.mask, 0.95)),
        reviewer=Unchanged(),
        models=VerifierModels(segment=MODEL, review="agent-model-from-settings"),
        thresholds=Thresholds(),
    )
    assert report.status == "passed"
