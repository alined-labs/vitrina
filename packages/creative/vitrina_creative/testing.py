"""Synthetic seller photos and generated scenes for the fidelity verifier (T033, T072).

The product is a red bag: a rounded body, a handle and a small white logo. Good scenes move,
scale and relight that same bag over new backgrounds. Altered scenes change what a buyer would
receive: its colour, shape, proportions or the number of units. Everything is drawn in code
(no binaries in the repository) and deterministic, and every scene carries the product mask a
perfect segmenter would return.
"""

from dataclasses import dataclass
from functools import cache

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from vitrina_core.providers import Blob
from vitrina_creative.imaging import encode_png

Colour = tuple[int, int, int]

RED: Colour = (178, 34, 52)
LOGO: Colour = (245, 245, 240)
SIZE = (512, 512)


@dataclass(frozen=True)
class SyntheticScene:
    name: str
    image: Blob
    mask: Blob


def bag(
    colour: Colour = RED,
    *,
    scale: float = 1.0,
    stretch: tuple[float, float] = (1.0, 1.0),
    handle: bool = True,
    logo: bool = True,
    count: int = 1,
) -> Image.Image:
    """The bag on a transparent layer cropped to its size; ``count`` bags side by side."""
    width, height = round(200 * scale * stretch[0]), round(150 * scale * stretch[1])
    handle_height = round(70 * scale) if handle else 0
    pad = 4
    cell = width + 2 * pad
    layer = Image.new("RGBA", (cell * count, height + handle_height + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for index in range(count):
        left, body_top = index * cell + pad, pad + handle_height
        if handle:
            span = round(width * 0.5)
            x0 = left + (width - span) // 2
            draw.arc(
                (x0, pad, x0 + span, pad + 2 * handle_height),
                start=180,
                end=360,
                fill=(*colour, 255),
                width=max(3, round(12 * scale)),
            )
        draw.rounded_rectangle(
            (left, body_top, left + width, body_top + height),
            radius=round(24 * scale),
            fill=(*colour, 255),
        )
        if logo:
            logo_width, logo_height = round(40 * scale), round(20 * scale)
            x0, y0 = left + (width - logo_width) // 2, body_top + round(height * 0.55)
            draw.rectangle((x0, y0, x0 + logo_width, y0 + logo_height), fill=(*LOGO, 255))
    return layer


def backdrop(kind: str, seed: int = 0) -> Image.Image:
    width, height = SIZE
    rng = np.random.default_rng(seed)
    if kind == "beige":
        return Image.new("RGB", SIZE, (236, 226, 208))
    if kind == "teal":
        return Image.new("RGB", SIZE, (32, 120, 128))
    if kind == "gradient":
        top, bottom = np.array([250, 240, 230]), np.array([170, 195, 220])
        rows = np.linspace(0.0, 1.0, height)[:, None, None]
        pixels = (top + (bottom - top) * rows) * np.ones((1, width, 1))
        return Image.fromarray(pixels.astype(np.uint8))
    if kind == "noise":
        return Image.fromarray(rng.integers(90, 200, (height, width, 3), dtype=np.uint8))
    if kind == "wood":
        stripes = 120 + 30 * np.sin(np.arange(width) / 9.0)
        pixels = np.stack([stripes, stripes * 0.7, stripes * 0.45], axis=-1)
        return Image.fromarray(np.repeat(pixels[None], height, axis=0).astype(np.uint8))
    if kind == "marble":
        pixels = 225 + rng.normal(0, 8, (height, width, 1)) * np.ones((1, 1, 3))
        return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    raise ValueError(f"unknown backdrop {kind!r}")


def place(
    name: str,
    background: Image.Image,
    layer: Image.Image,
    centre: tuple[int, int],
    light: float = 1.0,
    cast: tuple[float, float, float] | None = None,
) -> SyntheticScene:
    scene = background.copy()
    x, y = centre[0] - layer.width // 2, centre[1] - layer.height // 2
    scene.paste(layer, (x, y), layer)
    if light != 1.0:
        scene = ImageEnhance.Brightness(scene).enhance(light)
    if cast is not None:  # a coloured light such as golden hour: per-channel gains
        pixels = np.asarray(scene, dtype=np.float64) * np.array(cast)
        scene = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    mask = Image.new("L", SIZE, 0)
    mask.paste(layer.getchannel("A"), (x, y))
    return SyntheticScene(
        name=name,
        image=encode_png(np.asarray(scene, dtype=np.uint8)),
        mask=encode_png(np.asarray(mask, dtype=np.uint8) > 127),
    )


@cache
def source_photo() -> SyntheticScene:
    """The seller's photo: the bag on a plain beige table."""
    return place("source", backdrop("beige"), bag(), (256, 270))


@cache
def good_scenes() -> tuple[SyntheticScene, ...]:
    """Ten scenes a buyer would accept: same bag, new place, size, light or light colour."""
    return (
        place("teal-backdrop", backdrop("teal"), bag(), (256, 270)),
        place("smaller-left", backdrop("gradient"), bag(scale=0.7), (170, 320)),
        place("larger", backdrop("beige"), bag(scale=1.2), (256, 256)),
        place("noise-backdrop", backdrop("noise", seed=1), bag(scale=0.9), (300, 280)),
        place("brighter", backdrop("marble", seed=3), bag(), (256, 270), light=1.08),
        place("darker", backdrop("wood"), bag(scale=0.8), (240, 300), light=0.92),
        place("corner", backdrop("gradient"), bag(scale=0.6), (370, 370)),
        place("story-high", backdrop("teal"), bag(scale=0.85), (256, 190)),
        place("small-on-noise", backdrop("noise", seed=2), bag(scale=0.65), (200, 200)),
        # The hardest good case: a strong warm cast moves color_delta to about 0.17 of 0.25.
        place(
            "golden-hour", backdrop("beige"), bag(scale=1.1), (256, 262), cast=(1.15, 1.02, 0.75)
        ),
    )


@cache
def altered_scenes() -> tuple[SyntheticScene, ...]:
    """Ten scenes the numeric signals alone must block: colour, shape, proportions, count."""
    return (
        place("blue", backdrop("teal"), bag((40, 70, 170)), (256, 270)),
        place("green", backdrop("beige"), bag((40, 140, 70)), (256, 270)),
        place("brown", backdrop("marble", seed=4), bag((120, 72, 40), scale=0.9), (256, 270)),
        place("black", backdrop("gradient"), bag((34, 32, 36)), (256, 270)),
        place("pink", backdrop("beige"), bag((236, 130, 170), scale=0.8), (250, 280)),
        place("no-handle", backdrop("teal"), bag(handle=False), (256, 300)),
        place("stretched-wide", backdrop("beige"), bag(stretch=(1.45, 1.0)), (256, 270)),
        place("squashed", backdrop("noise", seed=5), bag(stretch=(1.0, 0.6)), (256, 270)),
        place("two-bags", backdrop("beige"), bag(scale=0.55, count=2), (256, 270)),
        place("tall-narrow", backdrop("wood"), bag(stretch=(0.7, 1.3)), (256, 260)),
    )


@cache
def logo_removed_scene() -> SyntheticScene:
    """Same shape and nearly the same colours, but no logo: only the visual review sees it."""
    return place("no-logo", backdrop("teal"), bag(logo=False), (256, 270))
