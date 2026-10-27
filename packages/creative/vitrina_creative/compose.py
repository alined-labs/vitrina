"""Compose stage (T032): Muse Image draws ad scenes around the seller's real product.

plan.md §4.2 step 2: one request per scene, with the product cut-out as the reference image, the
format's size, and a scene direction that varies from scene to scene (a Wonder brief's
``scene_brief`` leads when there is one). Every prompt tells the model to leave the product exactly
as it is; the fidelity verifier then checks that it did (FR-012), so the prompt is a request and
the verifier is the gate.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from vitrina_core.providers import Blob, ImageModel, ImageRequest, with_backoff

CreativeFormat = Literal["story_9x16", "catalog_1x1", "feed_4x5"]

FORMAT_SIZES: dict[CreativeFormat, tuple[int, int]] = {
    "story_9x16": (1080, 1920),
    "catalog_1x1": (1080, 1080),
    "feed_4x5": (1080, 1350),
}
FORMAT_LAYOUTS: dict[CreativeFormat, str] = {
    "story_9x16": "a vertical 9:16 story, the product in the lower middle third with space above it",
    "catalog_1x1": "a square 1:1 catalog photo, the product centred and filling about half the frame",
    "feed_4x5": "a vertical 4:5 feed photo, the product slightly below centre",
}
# Scene directions rotate so the scenes of one job differ (plan.md §4.2: 3 per format by default).
SCENE_DIRECTIONS = (
    "on a light wooden table by a window with soft natural daylight",
    "on a plain soft-coloured backdrop with a gentle shadow, studio light",
    "in a warm, softly blurred Colombian market setting with daylight",
    "on a linen cloth with a small green plant beside it, calm morning light",
    "on a smooth stone surface with a clean, bright background",
)
PRODUCT_RULE = (
    "Use the reference image as the exact product: keep its shape, proportions, colours, "
    "material, logos, labels and every part unchanged; show exactly one unit; do not add, "
    "remove or redraw anything on it."
)
SCENE_RULE = "No text, no watermark, no people's faces."


@dataclass(frozen=True)
class ComposedScene:
    format: CreativeFormat
    image: Blob
    scene_prompt: str


def scene_prompt(product_name: str, image_format: CreativeFormat, direction: str) -> str:
    return (
        f"Advertising photo of {product_name}, {FORMAT_LAYOUTS[image_format]}, {direction}. "
        f"{PRODUCT_RULE} {SCENE_RULE}"
    )


async def compose_scenes(
    cutout: Blob,
    *,
    product_name: str,
    formats: Sequence[CreativeFormat],
    scenes_per_format: int,
    scene_brief: str | None = None,
    image_model: ImageModel,
    model: str,
) -> list[ComposedScene]:
    """``scenes_per_format`` scenes for each format, in order; provider errors propagate."""
    scenes: list[ComposedScene] = []
    for image_format in formats:
        width, height = FORMAT_SIZES[image_format]
        for index in range(scenes_per_format):
            direction = (
                scene_brief
                if scene_brief and index == 0
                else SCENE_DIRECTIONS[index % len(SCENE_DIRECTIONS)]
            )
            prompt = scene_prompt(product_name, image_format, direction)
            request = ImageRequest(
                model=model,
                purpose="scene",
                prompt=prompt,
                references=(cutout,),
                size=f"{width}x{height}",
                count=1,
            )
            response = await with_backoff(partial(image_model.generate, request))
            scenes.extend(
                ComposedScene(image_format, image, prompt) for image in response.images[:1]
            )
    return scenes
