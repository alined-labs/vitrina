import base64
import io
import sys

import pytest
from PIL import Image

from vitrina_core.providers import (
    Blob,
    ImageRequest,
    ProviderError,
    RateLimitedError,
    SegmentRequest,
)
from vitrina_core.settings import Settings
from vitrina_meta_model_api import MetaImageModel, MetaSegmenter, meta_client
from vitrina_meta_model_api.testing import FakeApi, error, sam

# Two objects in a 320×334 image, the example line from meta-sam-parser's README; <C> marks
# where each confidence goes (the base85 payloads contain braces, so no str.format).
FIRST = (
    "0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334<C>|>"
    "<|mask;x=0;y=0<C>;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
)
SECOND = (
    "1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334<C>|>"
    "<|mask;x=0;y=0<C>;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>"
)


def line(first: float, second: float) -> str:
    return f"<0f>{FIRST.replace('<C>', f';c={first}')},{SECOND.replace('<C>', f';c={second}')}\n"


def photo(size: tuple[int, int] = (320, 334)) -> Blob:
    buffer = io.BytesIO()
    Image.new("RGB", size, (240, 240, 240)).save(buffer, "PNG")
    return Blob(data=buffer.getvalue(), mime_type="image/png")


def pixels(mask: Blob) -> Image.Image:
    return Image.open(io.BytesIO(mask.data)).convert("L")


async def test_as_2_1_a_label_is_sent_as_the_prompt_next_to_the_photo(api: FakeApi) -> None:
    api.answer(sam(line(0.4, 0.9)))
    image = photo()

    response = await MetaSegmenter(api.client()).segment(
        SegmentRequest(model="sam-from-settings", image=image, prompt="mochila")
    )

    assert api.requests[0].url == "https://meta.test/v1/responses"
    assert api.body() == {
        "model": "sam-from-settings",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "mochila"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(image.data).decode(),
                    },
                ],
            }
        ],
        "metadata": {"include_confidence": "true"},
    }
    meta = response.meta
    assert (meta.provider, meta.model, meta.purpose) == (
        "meta_model_api",
        "sam-from-settings",
        "segment",
    )


async def test_as_2_2_masks_come_back_full_size_best_score_first(api: FakeApi) -> None:
    api.answer(sam(line(0.4, 0.9)))

    response = await MetaSegmenter(api.client()).segment(
        SegmentRequest(model="m", image=photo(), prompt="mochila")
    )

    best, other = response.segments
    assert (best.score, other.score) == (0.9, 0.4)
    assert best.bbox == (155, 228, 203, 255)
    assert other.bbox == (211, 228, 271, 255)
    assert best.label == "mochila"
    mask = pixels(best.mask)
    assert mask.size == (320, 334)
    # Every product pixel lies inside the object's box; the rest of the photo is empty.
    assert mask.getbbox() is not None
    left, top, right, bottom = mask.getbbox()
    assert (left, top) >= (155, 228)
    assert (right, bottom) <= (203, 255)
    assert set(mask.get_flattened_data()) == {0, 255}


async def test_a_box_prompt_names_the_box_in_the_photos_pixels(api: FakeApi) -> None:
    api.answer(sam(line(0.8, 0.7)))

    await MetaSegmenter(api.client()).segment(
        SegmentRequest(model="m", image=photo(), box=(10, 20, 40, 60))
    )

    # Boxes are half-open here, as in mask_bbox; the wire's corners are inclusive.
    text = api.body()["input"][0]["content"][0]["text"]
    assert text == "<0f>1<|box;x1=10;y1=20;x2=39;y2=59;w=320;h=334|>"


async def test_as_2_3_no_match_is_an_empty_answer_not_an_error(api: FakeApi) -> None:
    api.answer(sam(""))

    response = await MetaSegmenter(api.client()).segment(
        SegmentRequest(model="m", image=photo(), prompt="mochila")
    )

    assert response.segments == ()


async def test_text_without_a_mask_is_a_final_provider_error(api: FakeApi) -> None:
    api.answer(sam("I cannot segment this image."))

    with pytest.raises(ProviderError) as caught:
        await MetaSegmenter(api.client()).segment(
            SegmentRequest(model="m", image=photo(), prompt="mochila")
        )

    assert not caught.value.retryable


async def test_as_3_11_segmentation_rate_limits_are_retryable(api: FakeApi) -> None:
    api.answer(error(429, "slow down", **{"retry-after": "1"}))

    with pytest.raises(RateLimitedError):
        await MetaSegmenter(api.client()).segment(
            SegmentRequest(model="m", image=photo(), prompt="mochila")
        )


async def test_a_request_without_prompt_or_box_is_refused_before_any_call(api: FakeApi) -> None:
    with pytest.raises(ValueError, match="prompt or a box"):
        await MetaSegmenter(api.client()).segment(SegmentRequest(model="m", image=photo()))

    assert not api.requests


@pytest.mark.live
async def test_live_sam_finds_the_product_muse_image_placed() -> None:
    """Muse Image places a synthetic bag, SAM finds it again (no customer data, Art. V.2):

    uv run --env-file .env pytest -m live providers/meta_model_api/tests/test_segmentation.py
    """
    settings = Settings()
    client = meta_client(settings)
    picture = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    picture.paste((200, 40, 40, 255), (48, 64, 208, 224))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    cutout = Blob(data=buffer.getvalue(), mime_type="image/png")
    scene = await MetaImageModel(client).generate(
        ImageRequest(
            model=settings.model_id("image"),
            prompt="Place this exact red handbag on a wooden shop counter. Keep its shape and color.",
            references=(cutout,),
            size="1024x1024",
        )
    )
    response = await MetaSegmenter(client).segment(
        SegmentRequest(model=settings.model_id("segment"), image=scene.images[0], prompt="handbag")
    )

    assert response.segments
    best = response.segments[0]
    assert pixels(best.mask).size == Image.open(io.BytesIO(scene.images[0].data)).size
    sys.stdout.write(f"\nsam: {len(response.segments)} segments, best {best.bbox} {best.score}\n")
