"""Scene images (Muse Image) for the creative compose stage (plan.md §4.2 step 2).

A request with references goes to ``/images/edits`` with each reference as a data URI, the
documented JSON body; one without goes to ``/images/generations``. ``size`` only sets the
aspect ratio: the output resolution is the model's own (1600×1600 on the first live call).
Images come back as base64 PNG, so the verifier reads them losslessly.
"""

import base64
from typing import Any

from openai import AsyncOpenAI
from openai.types import ImagesResponse

from vitrina_core.providers import (
    Blob,
    CallMeta,
    ImageRequest,
    ImageResponse,
    ProviderName,
    ProviderUnavailableError,
)
from vitrina_meta_model_api.client import call, tokens

OUTPUT_FORMAT = "png"


class MetaImageModel:
    """``ImageModel`` on the Meta Model API images endpoints (ADR-0003)."""

    def __init__(self, client: AsyncOpenAI, *, provider: ProviderName = "meta_model_api") -> None:
        self.client = client
        self.provider = provider

    async def generate(self, request: ImageRequest) -> ImageResponse:
        path = "/images/edits" if request.references else "/images/generations"
        body = _body(request)
        response, latency_ms = await call(
            self.provider,
            lambda: self.client.post(path, body=body, cast_to=ImagesResponse),
        )
        encoded = [item.b64_json for item in response.data or () if item.b64_json]
        if not encoded:
            raise ProviderUnavailableError(
                f"{self.provider} answered without an image", provider=self.provider
            )
        return ImageResponse(
            images=tuple(
                Blob(data=base64.b64decode(item), mime_type=f"image/{OUTPUT_FORMAT}")
                for item in encoded
            ),
            meta=CallMeta(
                provider=self.provider,
                model=request.model,
                purpose=request.purpose,
                tokens_in=tokens(response.usage, "input_tokens"),
                tokens_out=tokens(response.usage, "output_tokens"),
                latency_ms=latency_ms,
            ),
        )


def _body(request: ImageRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "n": request.count,
        "response_format": "b64_json",
        "output_format": OUTPUT_FORMAT,
    }
    if request.size is not None:
        body["size"] = request.size
    if request.references:
        body["images"] = [{"image_url": data_uri(blob)} for blob in request.references]
    return body


def data_uri(blob: Blob) -> str:
    return f"data:{blob.mime_type};base64,{base64.b64encode(blob.data).decode('ascii')}"
