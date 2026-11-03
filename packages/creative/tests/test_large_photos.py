"""FR-012 on large photos: the verifier's own arrays stay small, its model requests do not change.

Uploads go up to 40 megapixels (FR-010) and the worker has 1 GiB. The shape and colour signals
run at a working resolution; SAM and the visual review still get what they always got.
"""

import json
import subprocess
import sys
import tracemalloc
from io import BytesIO
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from vitrina_core.providers import (
    Blob,
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
from vitrina_creative import verifier
from vitrina_creative.api import SourceProduct, Thresholds, VerifierModels, verify_scene
from vitrina_creative.imaging import (
    TooManyPixelsError,
    color_histogram,
    decode_mask,
    decode_rgb,
    encode_png,
    mask_bbox,
    product_crop,
)
from vitrina_creative.media import IMAGE_MAX_PIXELS
from vitrina_creative.testing import good_scenes, source_photo

MODELS = VerifierModels(segment="segment-model-from-settings", review="agent-model-from-settings")
# The worker has 1 GiB and more loaded than this test's process.
PEAK_LIMIT_MIB = 768


class OneMask:
    def __init__(self, mask: Blob) -> None:
        self.mask = mask
        self.requests: list[SegmentRequest] = []

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        self.requests.append(request)
        box = mask_bbox(decode_mask(self.mask)) or (0, 0, 1, 1)
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return SegmentResponse(segments=(Segment(mask=self.mask, bbox=box, score=0.97),), meta=meta)


class Unchanged:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        text = json.dumps({"altered": False, "reason": "sin cambios en el producto"})
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return ChatResponse(message=ChatMessage(role="assistant", content=text), meta=meta)


def enlarged(factor: int) -> SourceProduct:
    """The synthetic seller's photo at ``factor`` times its size, with its mask."""
    photo = source_photo()
    with Image.open(BytesIO(photo.image.data)) as image:
        side = image.width * factor
        big = image.convert("RGB").resize((side, side), Image.Resampling.BICUBIC)
    with Image.open(BytesIO(photo.mask.data)) as mask:
        big_mask = mask.convert("L").resize((side, side), Image.Resampling.NEAREST)
    buffer = BytesIO()
    big.save(buffer, format="JPEG", quality=90)
    return SourceProduct(
        image=Blob(data=buffer.getvalue(), mime_type="image/jpeg"),
        mask=encode_png(np.asarray(big_mask, dtype=np.uint8) > 127),
    )


async def test_fr_012_the_signals_run_at_the_working_size_and_the_models_get_the_same_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, scene = enlarged(4), good_scenes()[0]
    sizes: list[tuple[tuple[int, ...], ...]] = []
    real_delta, real_iou = verifier.color_delta, verifier.aligned_iou

    def spy_delta(
        source_pixels: npt.NDArray[np.uint8],
        source_mask: npt.NDArray[np.bool_],
        scene_pixels: npt.NDArray[np.uint8],
        scene_mask: npt.NDArray[np.bool_],
    ) -> float:
        sizes.append((source_pixels.shape, source_mask.shape, scene_pixels.shape, scene_mask.shape))
        return real_delta(source_pixels, source_mask, scene_pixels, scene_mask)

    def spy_iou(source_mask: npt.NDArray[np.bool_], scene_mask: npt.NDArray[np.bool_]) -> float:
        sizes.append((source_mask.shape, scene_mask.shape))
        return real_iou(source_mask, scene_mask)

    monkeypatch.setattr(verifier, "color_delta", spy_delta)
    monkeypatch.setattr(verifier, "aligned_iou", spy_iou)
    sam, reviewer = OneMask(scene.mask), Unchanged()

    await verify_scene(
        source,
        scene.image,
        segmenter=sam,
        reviewer=reviewer,
        models=MODELS,
        thresholds=Thresholds(long_edge_px=300),
    )

    assert [shape[:2] for shapes in sizes for shape in shapes[:2]] == [(300, 300)] * 4
    # What the models receive does not depend on the working size: the scene as it came, and
    # the product cropped from the full frame.
    assert sam.requests[0].image == scene.image
    content = reviewer.requests[0].messages[1].content
    assert isinstance(content, tuple)
    parts = [part for part in content if isinstance(part, ImagePart)]
    full = product_crop(decode_rgb(source.image), decode_mask(source.mask))
    assert parts[0].image.data == full.data
    assert parts[1].image == scene.image


async def test_fr_012_a_large_photo_scores_like_the_same_photo_at_its_usual_size() -> None:
    photo, scene = source_photo(), good_scenes()[0]

    async def scores(source: SourceProduct, thresholds: Thresholds) -> tuple[float, float]:
        report = await verify_scene(
            source,
            scene.image,
            segmenter=OneMask(scene.mask),
            reviewer=Unchanged(),
            models=MODELS,
            thresholds=thresholds,
        )
        assert report.model_dump()["thresholds"] == {"mask_iou_min": 0.85, "color_delta_max": 0.25}
        return report.mask_iou, report.color_delta

    small = await scores(SourceProduct(image=photo.image, mask=photo.mask), Thresholds())
    large = await scores(enlarged(6), Thresholds(long_edge_px=2048))
    assert large[0] == pytest.approx(small[0], abs=0.02)
    # JPEG and averaging soften the colours a little; the limit that matters is 0.25.
    assert large[1] == pytest.approx(small[1], abs=0.05)


def test_fr_012_a_photo_within_the_working_size_is_decoded_exactly_as_before() -> None:
    photo = source_photo()
    assert (decode_rgb(photo.image, long_edge=512) == decode_rgb(photo.image)).all()
    assert (decode_mask(photo.mask, long_edge=512) == decode_mask(photo.mask)).all()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_fr_012_every_format_is_reduced_on_decode_keeping_its_shape(fmt: str) -> None:
    buffer = BytesIO()
    Image.new("RGB", (3000, 1000), (178, 34, 52)).save(buffer, format=fmt)
    blob = Blob(data=buffer.getvalue(), mime_type="image/png")
    assert decode_rgb(blob, long_edge=2048).shape == (683, 2048, 3)
    assert decode_mask(blob, long_edge=2048).shape == (683, 2048)


def test_fr_012_too_many_pixels_are_still_refused_before_decoding() -> None:
    side = int(IMAGE_MAX_PIXELS**0.5) + 1
    buffer = BytesIO()
    Image.new("1", (side, side)).save(buffer, format="PNG")
    blob = Blob(data=buffer.getvalue(), mime_type="image/png")
    with pytest.raises(TooManyPixelsError):
        decode_rgb(blob, long_edge=2048)
    with pytest.raises(TooManyPixelsError):
        decode_mask(blob, long_edge=2048)


def test_fr_012_the_colour_check_allocates_little_at_the_working_size() -> None:
    """Numpy reports its allocations to tracemalloc, so this bound does not depend on the machine:
    a working frame of 2048x1536 with the whole frame as product needs about 190 MiB in 64-bit
    floats and about 120 MiB as it is now.
    """
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 255, (1536, 2048, 3), dtype=np.uint8)
    mask = np.ones((1536, 2048), dtype=np.bool_)
    tracemalloc.start()
    try:
        histogram = color_histogram(pixels, mask)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert histogram.sum() == pytest.approx(1.0)
    assert peak < 150 * 1024 * 1024


def test_fr_012_the_working_size_is_a_setting_that_is_never_stored_on_a_report() -> None:
    assert Settings(_env_file=None).verifier_long_edge_px == 2048  # type: ignore[call-arg]
    thresholds = Thresholds.from_settings(
        Settings(_env_file=None, verifier_long_edge_px=1024)  # type: ignore[call-arg]
    )
    assert thresholds.long_edge_px == 1024
    assert thresholds.model_dump() == {"mask_iou_min": 0.85, "color_delta_max": 0.25}


PEAK_RUN = """
import asyncio, resource, sys, json
from types import SimpleNamespace as NS
from vitrina_core.providers import Blob
from vitrina_creative.verifier import SourceProduct, Thresholds, VerifierModels, verify_scene

def blob(path, mime):
    with open(path, "rb") as file:
        return Blob(data=file.read(), mime_type=mime)

photo, mask, scene, scene_mask = sys.argv[1:5]

class Sam:
    async def segment(self, request):
        return NS(segments=(NS(mask=blob(scene_mask, "image/png"), score=0.9),))

class Review:
    async def complete(self, request):
        return NS(message=NS(content='{"altered": false, "reason": "sin cambios"}'))

asyncio.run(verify_scene(
    SourceProduct(image=blob(photo, "image/jpeg"), mask=blob(mask, "image/png")),
    blob(scene, "image/jpeg"),
    segmenter=Sam(),
    reviewer=Review(),
    models=VerifierModels(segment="s", review="r"),
    thresholds=Thresholds(),
))
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="ru_maxrss is in KiB on Linux")
def test_fr_012_verifying_a_40_megapixel_photo_fits_in_the_worker(tmp_path: Path) -> None:
    """A fresh process, as a retried job would be. It peaks near 500 MiB and used to peak above
    2 GiB, so the bound sits far from both and allocator noise cannot trip it."""
    width, height = 7302, 5477
    assert width * height <= IMAGE_MAX_PIXELS
    rng = np.random.default_rng(40)
    tile = rng.integers(0, 255, (height // 8 + 1, width // 8 + 1, 3), dtype=np.uint8)
    Image.fromarray(tile).resize((width, height), Image.Resampling.NEAREST).save(
        tmp_path / "photo.jpg", quality=85
    )
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[height // 8 : 7 * height // 8, width // 8 : 7 * width // 8] = 255
    Image.fromarray(mask).save(tmp_path / "mask.png")
    Image.fromarray(tile[:400, :400]).resize((1600, 1600), Image.Resampling.NEAREST).save(
        tmp_path / "scene.jpg", quality=85
    )
    scene_mask = np.zeros((1600, 1600), dtype=np.uint8)
    scene_mask[200:1400, 200:1400] = 255
    Image.fromarray(scene_mask).save(tmp_path / "scene.png")

    run = subprocess.run(
        [
            sys.executable,
            "-c",
            PEAK_RUN,
            *(str(tmp_path / name) for name in ("photo.jpg", "mask.png", "scene.jpg", "scene.png")),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    assert int(run.stdout.strip().splitlines()[-1]) < PEAK_LIMIT_MIB
