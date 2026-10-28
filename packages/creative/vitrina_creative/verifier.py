"""Fidelity verifier (T033): does a generated scene still show the seller's real product?

FR-012, ADR-0007, plan.md §4.2 step 3. Three signals, all recorded on every report:

1. ``mask_iou``: SAM re-segments the scene and the product mask is aligned to the source mask by
   bounding box; blocked below ``mask_iou_min`` (default 0.85).
2. ``color_delta``: Bhattacharyya distance of the HSV histograms inside the product region;
   blocked above ``color_delta_max`` (default 0.25).
3. ``visual_review``: Spark vision answers "¿El producto cambió?"; blocked when it says yes, and
   when it cannot answer (fail closed, constitution Art. IV.1).

Any failing signal blocks the scene. Blocked scenes stay stored for audit and can never be
approved (FR-013; the ads trigger in the baseline migration refuses them as well).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from vitrina_core.providers import (
    Blob,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ContentPart,
    ImagePart,
    ResponseFormat,
    Segmenter,
    SegmentRequest,
    TextPart,
    with_backoff,
)
from vitrina_core.settings import Settings
from vitrina_creative.imaging import (
    Mask,
    aligned_iou,
    color_delta,
    decode_mask,
    decode_rgb,
    fit_mask,
    product_crop,
)

REVIEW_PROMPT = """Eres un inspector de fidelidad de producto para anuncios. Comparas dos imágenes:
IMAGEN 1: recorte del producto real, tomado de la foto original del vendedor.
IMAGEN 2: escena publicitaria generada a partir de ese producto.

Pregunta única: ¿El producto cambió?
Revisa uno por uno: forma y proporciones · color y acabado · logotipos, textos y etiquetas · material y textura · cantidad de unidades · partes añadidas o eliminadas.
Ignora: fondo, iluminación, sombras, reflejos, ángulo de cámara, tamaño en el encuadre y elementos decorativos que no toquen el producto.

Criterio: si cualquiera de los seis aspectos cambió de modo que el comprador recibiría algo distinto de lo que ve, altered=true. Si no puedes decidir, altered=true.

Responde solo con JSON y nada más:
{"altered": true, "reason": "color del bolso alterado"}
- altered: true o false.
- reason: máximo 120 caracteres, en español, el aspecto concreto que cambió. Si altered=false, escribe "sin cambios en el producto"."""

REVIEW_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["altered", "reason"],
    "properties": {
        "altered": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 120},
    },
}

REASON_MAX = 120


class VisualReview(BaseModel):
    altered: bool
    reason: str


INCONCLUSIVE = VisualReview(altered=True, reason="revisión visual no concluyente")


class Thresholds(BaseModel):
    mask_iou_min: float = 0.85
    color_delta_max: float = 0.25

    @classmethod
    def from_settings(cls, settings: Settings) -> Thresholds:
        return cls(mask_iou_min=settings.mask_iou_min, color_delta_max=settings.color_delta_max)


class Verification(BaseModel):
    """openapi.yaml ``Verification``: stored on ``creatives.verification``, returned by the API."""

    status: Literal["passed", "blocked"]
    mask_iou: float = Field(ge=0, le=1)
    color_delta: float = Field(ge=0)
    visual_review: VisualReview
    thresholds: Thresholds
    reason: str
    checked_at: datetime


@dataclass(frozen=True)
class SourceProduct:
    """The product as the segment stage found it in the seller's photo."""

    image: Blob
    mask: Blob
    # What SAM is asked to find in the scene: an English noun, e.g. "woven bag" (concept.py).
    label: str = "producto"
    # "de" + article + noun for the Spanish reason: "del bolso", "de la camiseta".
    possessive: str = "del producto"


@dataclass(frozen=True)
class VerifierModels:
    """Model IDs from settings (``MODEL_SEGMENT``, ``MODEL_AGENT``); never literals (Q01)."""

    segment: str
    review: str

    @classmethod
    def from_settings(cls, settings: Settings) -> VerifierModels:
        return cls(segment=settings.model_id("segment"), review=settings.model_id("agent"))


async def verify_scene(
    source: SourceProduct,
    scene: Blob,
    *,
    segmenter: Segmenter,
    reviewer: ChatModel,
    models: VerifierModels,
    thresholds: Thresholds,
    now: datetime | None = None,
) -> Verification:
    """Run the three signals on one generated scene and decide passed or blocked (FR-012)."""
    source_pixels = decode_rgb(source.image)
    source_mask = fit_mask(decode_mask(source.mask), *source_pixels.shape[:2])
    scene_pixels = decode_rgb(scene)
    scene_mask = await _find_product(scene, source.label, segmenter=segmenter, model=models.segment)
    review = await review_scene(
        product_crop(source_pixels, source_mask), scene, reviewer=reviewer, model=models.review
    )

    problems: list[str] = []
    if scene_mask is None:
        iou, delta = 0.0, 1.0
        problems.append("el producto no aparece en la escena")
    else:
        scene_mask = fit_mask(scene_mask, *scene_pixels.shape[:2])
        iou = aligned_iou(source_mask, scene_mask)
        delta = color_delta(source_pixels, source_mask, scene_pixels, scene_mask)
        if iou < thresholds.mask_iou_min:
            problems.append(f"forma {source.possessive} alterada")
        if delta > thresholds.color_delta_max:
            problems.append(f"color {source.possessive} alterado")
    if review.altered and review.reason not in problems:
        problems.append(review.reason)

    return Verification(
        status="blocked" if problems else "passed",
        mask_iou=round(iou, 4),
        color_delta=round(delta, 4),
        visual_review=review,
        thresholds=thresholds,
        reason=f"{', '.join(problems)} — bloqueado"
        if problems
        else "producto sin cambios — aprobado",
        checked_at=now or datetime.now(UTC),
    )


async def review_scene(crop: Blob, scene: Blob, *, reviewer: ChatModel, model: str) -> VisualReview:
    """Spark's yes/no review; one retry on malformed output, then fail closed."""
    content: tuple[ContentPart, ...] = (
        TextPart(text="IMAGEN 1: recorte del producto real."),
        ImagePart(image=crop),
        TextPart(text="IMAGEN 2: escena publicitaria generada."),
        ImagePart(image=scene),
        TextPart(text="¿El producto cambió?"),
    )
    request = ChatRequest(
        model=model,
        purpose="verify",
        messages=(
            ChatMessage(role="system", content=REVIEW_PROMPT),
            ChatMessage(role="user", content=content),
        ),
        response_format=ResponseFormat(name="visual_review", json_schema=REVIEW_SCHEMA),
        temperature=0.0,
    )
    for _attempt in range(2):
        response = await with_backoff(lambda: reviewer.complete(request))
        review = parse_review(response.message.content)
        if review is not None:
            return review
    return INCONCLUSIVE


def parse_review(content: object) -> VisualReview | None:
    """The review JSON, tolerating code fences or text around it; None when unusable."""
    if not isinstance(content, str):
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    altered, reason = data.get("altered"), data.get("reason")
    if not isinstance(altered, bool) or not isinstance(reason, str):
        return None
    reason = " ".join(reason.split())[:REASON_MAX]
    if not reason:
        reason = "cambio en el producto sin detalle" if altered else "sin cambios en el producto"
    return VisualReview(altered=altered, reason=reason)


async def _find_product(
    scene: Blob, label: str, *, segmenter: Segmenter, model: str
) -> Mask | None:
    request = SegmentRequest(model=model, purpose="verify", image=scene, prompt=label)
    response = await with_backoff(lambda: segmenter.segment(request))
    if not response.segments:
        return None
    best = max(response.segments, key=lambda segment: segment.score)
    mask = decode_mask(best.mask)
    return mask if mask.any() else None
