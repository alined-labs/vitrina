"""Segment stage (T031): find the product in a seller's photo before any scene is composed.

plan.md §4.2 step 1 and ADR-0007: SAM 3.1 returns a mask and a box for the product; a product
covering less than 5 % of the photo is rejected, because scenes built from it cannot be
verified reliably. The result feeds the compose stage (the cut-out) and the fidelity verifier
(the source mask).
"""

from dataclasses import dataclass

from vitrina_core.jobs import JobFailedError
from vitrina_core.providers import Blob, Box, Segmenter, SegmentRequest, with_backoff
from vitrina_creative.imaging import (
    TooManyPixelsError,
    decode_mask,
    decode_rgb,
    encode_png,
    fit_mask,
    mask_bbox,
    product_cutout,
)
from vitrina_creative.media import TOO_MANY_PIXELS
from vitrina_creative.verifier import SourceProduct

MIN_AREA = 0.05


class SegmentationError(JobFailedError):
    """A photo the pipeline cannot use: a final job failure whose message the seller reads."""


@dataclass(frozen=True)
class SegmentedPhoto:
    # The verifier's source: the photo, the product mask, the label and the possessive.
    product: SourceProduct
    bbox: Box
    # Share of the photo the product covers, 0 to 1.
    area: float
    # The product alone on a transparent background, cropped to its box.
    cutout: Blob


async def segment_photo(
    photo: Blob,
    *,
    label: str,
    possessive: str = "del producto",
    segmenter: Segmenter,
    model: str,
    min_area: float = MIN_AREA,
) -> SegmentedPhoto:
    """SAM on one source photo: the best-scoring product segment, or a SegmentationError."""
    try:
        pixels = decode_rgb(photo)
    except TooManyPixelsError as error:
        raise SegmentationError("PHOTO_TOO_LARGE", TOO_MANY_PIXELS) from error
    request = SegmentRequest(model=model, purpose="segment", image=photo, prompt=label)
    response = await with_backoff(lambda: segmenter.segment(request))
    if not response.segments:
        raise _not_found()
    best = max(response.segments, key=lambda segment: segment.score)
    mask = fit_mask(decode_mask(best.mask), *pixels.shape[:2])
    box = mask_bbox(mask)
    if box is None:
        raise _not_found()
    area = float(mask.mean())
    if area < min_area:
        raise SegmentationError(
            "PRODUCT_TOO_SMALL",
            f"El producto ocupa solo el {area * 100:.0f} % de la foto. Toma una foto más cercana "
            f"(mínimo {min_area * 100:.0f} %).",
        )
    product = SourceProduct(image=photo, mask=encode_png(mask), label=label, possessive=possessive)
    return SegmentedPhoto(
        product=product, bbox=box, area=round(area, 4), cutout=product_cutout(pixels, mask)
    )


def _not_found() -> SegmentationError:
    return SegmentationError(
        "PRODUCT_NOT_FOUND",
        "No encontramos el producto en la foto. Prueba con una foto donde se vea completo.",
    )
