"""Product masks (SAM 3.1) for the segment stage and the fidelity verifier (ADR-0007).

SAM runs on ``/responses``: one user message with the prompt as ``input_text`` and the photo as
a data-URI ``input_image``. It answers in its own text format, one line per frame with a box
and a base85 mask per object; Meta's ``meta-sam-parser`` reads it. Each mask covers only its
object's box, so the adapter pastes it into a full-size mask, the shape the pipeline expects.
Scenes reach SAM at Muse Image's 1600×1600 (several MB as base64), so SAM gets the image
calls' timeout too; it runs in creative jobs, never inside a customer's turn.
"""

import base64
from io import BytesIO
from typing import Any

from meta_sam_parser import (
    CompletedOutcome,
    FramePrompt,
    PromptBox,
    PromptObject,
    SegmentationMaskRecord,
    build_object_prompt,
    decode_mask_to_raster,
    image_segmentation_format,
)
from openai import AsyncOpenAI
from openai.types.responses import Response
from PIL import Image

from vitrina_core.providers import (
    Blob,
    CallMeta,
    ProviderError,
    ProviderName,
    Segment,
    SegmentRequest,
    SegmentResponse,
)
from vitrina_meta_model_api.client import call
from vitrina_meta_model_api.images import IMAGE_TIMEOUT_S


class MetaSegmenter:
    """``Segmenter`` on SAM 3.1 through the Meta Model API (ADR-0003)."""

    def __init__(self, client: AsyncOpenAI, *, provider: ProviderName = "meta_model_api") -> None:
        self.client = client
        self.provider = provider

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        size = _size(request.image)
        body = _body(request, size)
        response, latency_ms = await call(
            self.provider,
            lambda: self.client.post(
                "/responses", body=body, cast_to=Response, options={"timeout": IMAGE_TIMEOUT_S}
            ),
        )
        segments = self._segments(response.output_text, size, request.prompt)
        return SegmentResponse(
            segments=tuple(sorted(segments, key=lambda segment: -segment.score)),
            meta=CallMeta(
                provider=self.provider,
                model=request.model,
                purpose=request.purpose,
                latency_ms=latency_ms,
            ),
        )

    def _segments(self, text: str, size: tuple[int, int], label: str | None) -> list[Segment]:
        parser = image_segmentation_format().create_parser()
        parser.push(text)
        records = parser.finish(CompletedOutcome()).result.records
        masks = [record for record in records if isinstance(record, SegmentationMaskRecord)]
        if text.strip() and not masks:
            raise ProviderError(
                f"{self.provider} answered SAM text without a mask: {text[:120]!r}",
                provider=self.provider,
            )
        return [_segment(record, size, label) for record in masks]


def _size(image: Blob) -> tuple[int, int]:
    """The photo's (width, height) as displayed, the space SAM's coordinates live in."""
    with Image.open(BytesIO(image.data)) as picture:
        return picture.size


def _body(request: SegmentRequest, size: tuple[int, int]) -> dict[str, Any]:
    image = base64.b64encode(request.image.data).decode("ascii")
    return {
        "model": request.model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _prompt(request, size)},
                    {
                        "type": "input_image",
                        "image_url": f"data:{request.image.mime_type};base64,{image}",
                    },
                ],
            }
        ],
        # The metadata values are strings in SAM's API, not booleans.
        "metadata": {"include_confidence": "true"},
    }


def _prompt(request: SegmentRequest, size: tuple[int, int]) -> str:
    """A noun phrase, or a box prompt; SAM does not take both in one request."""
    if request.box is not None:
        left, top, right, bottom = request.box
        box = PromptBox(left, top, right, bottom)
        return build_object_prompt(
            size=size, objects=[PromptObject(id=1, prompts=[FramePrompt(frame=0, box=box)])]
        )
    if not request.prompt:
        raise ValueError("SAM needs a prompt or a box")
    return request.prompt


def _segment(record: SegmentationMaskRecord, size: tuple[int, int], label: str | None) -> Segment:
    bounds = record.bounds
    crop = Image.frombytes(
        "L",
        (record.mask.width, record.mask.height),
        bytes(255 if bit else 0 for bit in decode_mask_to_raster(record.mask)),
    )
    full = Image.new("L", size, 0)
    full.paste(crop, (bounds.left, bounds.top))
    buffer = BytesIO()
    full.save(buffer, format="PNG")
    return Segment(
        mask=Blob(data=buffer.getvalue(), mime_type="image/png"),
        bbox=(bounds.left, bounds.top, bounds.right, bounds.bottom),
        score=record.confidence if record.confidence is not None else 0.0,
        label=label,
    )
