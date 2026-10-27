"""Scene generation (Muse Image) and segmentation (SAM 3.1) for the creative pipeline."""

from typing import Protocol

from vitrina_core.providers.base import Blob, CallMeta, Frozen, ModelPurpose

# Pixel box as (x0, y0, x1, y1).
Box = tuple[int, int, int, int]


class ImageRequest(Frozen):
    model: str
    purpose: ModelPurpose = "scene"
    prompt: str
    references: tuple[Blob, ...] = ()
    size: str | None = None
    count: int = 1


class ImageResponse(Frozen):
    images: tuple[Blob, ...]
    meta: CallMeta


class ImageModel(Protocol):
    async def generate(self, request: ImageRequest) -> ImageResponse: ...


class SegmentRequest(Frozen):
    model: str
    purpose: ModelPurpose = "segment"
    image: Blob
    prompt: str | None = None
    box: Box | None = None


class Segment(Frozen):
    mask: Blob
    bbox: Box
    score: float
    label: str | None = None


class SegmentResponse(Frozen):
    segments: tuple[Segment, ...]
    meta: CallMeta


class Segmenter(Protocol):
    async def segment(self, request: SegmentRequest) -> SegmentResponse: ...
