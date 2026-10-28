import base64
import io
import sys

import pytest
from PIL import Image

from vitrina_core.providers import (
    Blob,
    ImageRequest,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
)
from vitrina_core.settings import Settings
from vitrina_meta_model_api import MetaImageModel, meta_client
from vitrina_meta_model_api.testing import FakeApi, error, images

CUTOUT = Blob(data=b"\x89PNG cutout", mime_type="image/png")


async def test_as_2_1_scenes_with_a_cutout_go_to_edits_with_a_data_uri(api: FakeApi) -> None:
    api.answer(images(b"scene-png", usage={"input_tokens": 18684, "output_tokens": 1437}))
    request = ImageRequest(
        model="image-from-settings",
        prompt="La mochila en una tienda",
        references=(CUTOUT,),
        size="1080x1350",
    )

    response = await MetaImageModel(api.client()).generate(request)

    assert api.requests[0].url == "https://meta.test/v1/images/edits"
    assert api.body() == {
        "model": "image-from-settings",
        "prompt": "La mochila en una tienda",
        "n": 1,
        "response_format": "b64_json",
        "output_format": "png",
        "size": "1080x1350",
        "images": [
            {"image_url": "data:image/png;base64," + base64.b64encode(CUTOUT.data).decode()}
        ],
    }
    assert response.images == (Blob(data=b"scene-png", mime_type="image/png"),)
    meta = response.meta
    assert (meta.provider, meta.model, meta.purpose) == (
        "meta_model_api",
        "image-from-settings",
        "scene",
    )
    assert (meta.tokens_in, meta.tokens_out, meta.cost_usd) == (18684, 1437, None)


async def test_a_prompt_without_references_goes_to_generations(api: FakeApi) -> None:
    api.answer(images(b"one", b"two"))

    response = await MetaImageModel(api.client()).generate(
        ImageRequest(model="m", prompt="fondo", count=2)
    )

    assert api.requests[0].url == "https://meta.test/v1/images/generations"
    body = api.body()
    assert "images" not in body
    assert "size" not in body
    assert body["n"] == 2
    assert [image.data for image in response.images] == [b"one", b"two"]
    assert (response.meta.tokens_in, response.meta.tokens_out) == (0, 0)


async def test_an_answer_without_image_data_is_retryable(api: FakeApi) -> None:
    api.answer(images())

    with pytest.raises(ProviderUnavailableError):
        await MetaImageModel(api.client()).generate(ImageRequest(model="m", prompt="p"))


async def test_as_3_11_image_rate_limits_are_retryable(api: FakeApi) -> None:
    api.answer(error(429, "slow down", **{"retry-after": "3"}))

    with pytest.raises(RateLimitedError) as caught:
        await MetaImageModel(api.client()).generate(ImageRequest(model="m", prompt="p"))

    assert caught.value.retry_after_s == 3.0


async def test_a_refused_prompt_is_final_and_keeps_the_reason(api: FakeApi) -> None:
    api.answer(error(400, "prompt rejected by moderation"))

    with pytest.raises(ProviderError) as caught:
        await MetaImageModel(api.client()).generate(ImageRequest(model="m", prompt="p"))

    assert not caught.value.retryable
    assert "prompt rejected by moderation" in str(caught.value)


@pytest.mark.live
async def test_live_muse_image_places_a_cutout_in_a_scene() -> None:
    """One edit call with a synthetic cutout (no customer data, Art. V.2):

    uv run --env-file .env pytest -m live providers/meta_model_api/tests/test_images.py
    """
    settings = Settings()
    model = settings.model_id("image")
    picture = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    picture.paste((30, 90, 200, 255), (64, 48, 192, 220))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    cutout = Blob(data=buffer.getvalue(), mime_type="image/png")

    response = await MetaImageModel(meta_client(settings)).generate(
        ImageRequest(
            model=model,
            prompt="Place this exact blue box on a wooden table. Keep its shape and color.",
            references=(cutout,),
            size="1024x1024",
        )
    )

    scene = Image.open(io.BytesIO(response.images[0].data))
    assert scene.format == "PNG"
    assert min(scene.size) >= 512
    sys.stdout.write(f"\nmuse image: {scene.size}, {response.meta}\n")
