"""T032 compose stage: one request per scene, the cut-out as reference, the product rule always."""

import pytest

from vitrina_core.providers import Blob, CallMeta, ImageRequest, ImageResponse
from vitrina_creative.api import compose_scenes
from vitrina_creative.compose import FORMAT_SIZES, PRODUCT_RULE

CUTOUT = Blob(data=b"\x89PNG cutout", mime_type="image/png")


class FakeMuse:
    def __init__(self) -> None:
        self.requests: list[ImageRequest] = []

    async def generate(self, request: ImageRequest) -> ImageResponse:
        self.requests.append(request)
        image = Blob(data=f"scene-{len(self.requests)}".encode(), mime_type="image/png")
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return ImageResponse(images=(image,), meta=meta)


async def compose(muse: FakeMuse, **kwargs: object):
    options = {
        "product_name": "a handmade red bag",
        "formats": ["story_9x16", "catalog_1x1"],
        "scenes_per_format": 3,
    }
    options.update(kwargs)
    return await compose_scenes(
        CUTOUT, image_model=muse, model="image-model-from-settings", **options
    )  # type: ignore[arg-type]


async def test_as_2_1_three_scenes_per_format_with_the_formats_size() -> None:
    muse = FakeMuse()
    scenes = await compose(muse)
    assert [scene.format for scene in scenes] == ["story_9x16"] * 3 + ["catalog_1x1"] * 3
    assert [r.size for r in muse.requests] == ["1080x1920"] * 3 + ["1080x1080"] * 3
    assert {r.purpose for r in muse.requests} == {"scene"}
    assert {r.model for r in muse.requests} == {"image-model-from-settings"}
    assert all(r.references == (CUTOUT,) for r in muse.requests)


async def test_every_prompt_asks_to_keep_the_product_unchanged_and_scenes_differ() -> None:
    muse = FakeMuse()
    scenes = await compose(muse, formats=["feed_4x5"], scenes_per_format=3)
    assert all(PRODUCT_RULE in scene.scene_prompt for scene in scenes)
    assert len({scene.scene_prompt for scene in scenes}) == 3
    assert "4:5" in scenes[0].scene_prompt


async def test_a_brief_direction_leads_the_first_scene() -> None:
    muse = FakeMuse()
    scenes = await compose(
        muse,
        formats=["catalog_1x1"],
        scenes_per_format=2,
        scene_brief="beside a gift box with a ribbon",
    )
    assert "beside a gift box with a ribbon" in scenes[0].scene_prompt
    assert "beside a gift box with a ribbon" not in scenes[1].scene_prompt


@pytest.mark.parametrize("image_format", sorted(FORMAT_SIZES))
async def test_each_contract_format_has_a_size(image_format: str) -> None:
    muse = FakeMuse()
    await compose(muse, formats=[image_format], scenes_per_format=1)
    width, height = FORMAT_SIZES[image_format]  # type: ignore[index]
    assert muse.requests[0].size == f"{width}x{height}"
