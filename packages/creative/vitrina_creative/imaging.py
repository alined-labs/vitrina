"""Pixel helpers for the fidelity verifier (T033): decoding, product masks, colour histograms.

Masks are boolean arrays (True where the product is); images are RGB ``uint8`` arrays. Every
function here is pure, so the fidelity suite runs without any provider (AS-7.3).
"""

from io import BytesIO

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageFilter

from vitrina_core.providers import Blob, Box

Mask = npt.NDArray[np.bool_]
Pixels = npt.NDArray[np.uint8]
Histogram = npt.NDArray[np.float64]

# Colour histogram layout (ADR-0007): 18 hue bins of 20° × 3 saturation levels for coloured
# pixels, then 4 grey levels for pixels without a reliable hue (low saturation or very dark).
HUE_BINS = 18
SAT_LEVELS = 3
GREY_LEVELS = 4
MIN_SATURATION = 0.20
MIN_VALUE = 0.15
# The mask is eroded by this many pixels first, so background at the product's edge never counts.
EDGE_PX = 2


def decode_rgb(blob: Blob) -> Pixels:
    with Image.open(BytesIO(blob.data)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def decode_mask(blob: Blob) -> Mask:
    """A mask image in any mode, as booleans: True where the product is."""
    with Image.open(BytesIO(blob.data)) as image:
        return np.asarray(image.convert("L"), dtype=np.uint8) > 127


def encode_png(array: Pixels | Mask) -> Blob:
    data = array.astype(np.uint8) * 255 if array.dtype == np.bool_ else array
    buffer = BytesIO()
    Image.fromarray(data).save(buffer, format="PNG")
    return Blob(data=buffer.getvalue(), mime_type="image/png")


def mask_bbox(mask: Mask) -> Box | None:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def fit_mask(mask: Mask, height: int, width: int) -> Mask:
    """The mask resized (nearest neighbour) to an image's size, when a model returns another."""
    if mask.shape == (height, width):
        return mask
    return _resize(mask, (width, height))


def aligned_iou(source: Mask, scene: Mask) -> float:
    """Overlap of two product masks after aligning them by bounding box (plan.md §4.2).

    Each mask is cropped to its bounding box. The scene crop is scaled by a single factor so both
    crops cover the same area; its aspect ratio is kept, so a stretched, trimmed or duplicated
    product loses overlap instead of being resized into agreement. Both crops are then centred
    on a common canvas and compared.
    """
    source_box, scene_box = mask_bbox(source), mask_bbox(scene)
    if source_box is None or scene_box is None:
        return 0.0
    a = _crop(source, source_box)
    b = _crop(scene, scene_box)
    factor = float(np.sqrt(a.size / b.size))
    b = _resize(b, (max(1, round(b.shape[1] * factor)), max(1, round(b.shape[0] * factor))))
    height, width = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    a, b = _centre(a, height, width), _centre(b, height, width)
    union = int(np.logical_or(a, b).sum())
    return int(np.logical_and(a, b).sum()) / union if union else 0.0


def color_histogram(pixels: Pixels, mask: Mask) -> Histogram:
    """Normalised HSV histogram of the product region (layout in the module constants)."""
    region = _erode(mask, EDGE_PX)
    if int(region.sum()) < 64:  # a tiny product: erosion would leave nothing to compare
        region = mask
    hsv = np.asarray(Image.fromarray(pixels).convert("HSV"), dtype=np.float64)[region] / 255.0
    hue, sat, val = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    coloured = (sat >= MIN_SATURATION) & (val >= MIN_VALUE)
    hue_bin = np.minimum((hue[coloured] * HUE_BINS).astype(np.int64), HUE_BINS - 1)
    sat_level = (sat[coloured] - MIN_SATURATION) / (1.0 - MIN_SATURATION) * SAT_LEVELS
    sat_bin = np.minimum(sat_level.astype(np.int64), SAT_LEVELS - 1)
    chroma = np.zeros((HUE_BINS, SAT_LEVELS), dtype=np.float64)
    np.add.at(chroma, (hue_bin, sat_bin), 1.0)
    # Share each hue bin with its neighbours on the hue circle, so a slight hue shift from the
    # scene's lighting moves weight between overlapping bins instead of across a hard edge.
    chroma = 0.5 * chroma + 0.25 * np.roll(chroma, 1, axis=0) + 0.25 * np.roll(chroma, -1, axis=0)
    grey_bin = np.minimum((val[~coloured] * GREY_LEVELS).astype(np.int64), GREY_LEVELS - 1)
    grey = np.bincount(grey_bin, minlength=GREY_LEVELS).astype(np.float64)
    histogram = np.concatenate([chroma.ravel(), grey])
    total = float(histogram.sum())
    return histogram / total if total else histogram


def bhattacharyya(p: Histogram, q: Histogram) -> float:
    """Bhattacharyya distance of two normalised histograms: 0 identical, 1 disjoint."""
    coefficient = float(np.sum(np.sqrt(p * q)))
    return float(np.sqrt(max(0.0, 1.0 - coefficient)))


def color_delta(source: Pixels, source_mask: Mask, scene: Pixels, scene_mask: Mask) -> float:
    return bhattacharyya(color_histogram(source, source_mask), color_histogram(scene, scene_mask))


def product_crop(pixels: Pixels, mask: Mask, margin: int = 8) -> Blob:
    """The product alone on white, cropped to its box: image 1 of the visual review."""
    box = mask_bbox(mask)
    if box is None:
        return encode_png(pixels)
    x0, y0, x1, y1 = box
    height, width = mask.shape
    x0, y0 = max(0, x0 - margin), max(0, y0 - margin)
    x1, y1 = min(width, x1 + margin), min(height, y1 + margin)
    cut = np.where(mask[..., None], pixels, np.uint8(255)).astype(np.uint8)
    return encode_png(cut[y0:y1, x0:x1])


def product_cutout(pixels: Pixels, mask: Mask) -> Blob:
    """The product alone on a transparent background, cropped to its box (compose reference)."""
    box = mask_bbox(mask)
    if box is None:
        raise ValueError("the mask is empty: there is no product to cut out")
    x0, y0, x1, y1 = box
    rgba = np.dstack([pixels, mask.astype(np.uint8) * 255])[y0:y1, x0:x1]
    return encode_png(np.ascontiguousarray(rgba))


def _crop(mask: Mask, box: Box) -> Mask:
    x0, y0, x1, y1 = box
    return mask[y0:y1, x0:x1]


def _resize(mask: Mask, size: tuple[int, int]) -> Mask:
    image = Image.fromarray(mask.astype(np.uint8) * 255)
    return np.asarray(image.resize(size, Image.Resampling.NEAREST), dtype=np.uint8) > 127


def _centre(mask: Mask, height: int, width: int) -> Mask:
    canvas = np.zeros((height, width), dtype=np.bool_)
    top, left = (height - mask.shape[0]) // 2, (width - mask.shape[1]) // 2
    canvas[top : top + mask.shape[0], left : left + mask.shape[1]] = mask
    return canvas


def _erode(mask: Mask, pixels: int) -> Mask:
    if pixels <= 0:
        return mask
    image = Image.fromarray(mask.astype(np.uint8) * 255).filter(
        ImageFilter.MinFilter(2 * pixels + 1)
    )
    return np.asarray(image, dtype=np.uint8) > 127
