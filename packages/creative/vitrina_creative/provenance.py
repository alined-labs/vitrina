"""Provenance and AI labels for generated images (constitution Art. IV.3, FR-015, AS-8.2).

Every generated scene carries machine-readable provenance: IPTC's digital source type in an XMP
packet (``trainedAlgorithmicMedia``, the field platforms read to show an AI label), plus PNG text
fields naming the model, the source photo and the verifier's result. Public pages and exports
also get a visible "Imagen generada con IA" label, on by default (``sellers.ai_label_enabled``).
No seller or customer data goes into an image.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from vitrina_core.providers import Blob

AI_LABEL = "Imagen generada con IA"
IPTC_SOURCE = "http://cv.iptc.org/newscodes/digitalsourcetype/"
SourceType = Literal["trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia"]
XMP_KEY = "XML:com.adobe.xmp"


@dataclass(frozen=True)
class Provenance:
    # The image model's ID from settings (never a literal).
    generator: str
    # SHA-256 of the seller's photo the scene was composed from.
    source_sha256: str
    verification: Literal["passed", "blocked", "pending"]
    created_at: datetime
    # A scene drawn entirely by the model, or the real cut-out placed in a generated scene.
    source_type: SourceType = "trainedAlgorithmicMedia"


def provenance_record(
    provenance: Provenance, *, creative_id: str, image_format: str
) -> dict[str, str]:
    """The ``provenance.json`` entry of an ad export (FR-015)."""
    return {
        "creative_id": creative_id,
        "format": image_format,
        "ai_generated": "true",
        "digital_source_type": IPTC_SOURCE + provenance.source_type,
        "generator": provenance.generator,
        "source_photo_sha256": provenance.source_sha256,
        "verification": provenance.verification,
        "created_at": provenance.created_at.isoformat(),
        "label": AI_LABEL,
    }


def provenance_json(records: list[dict[str, str]]) -> str:
    return json.dumps({"creatives": records}, ensure_ascii=False, indent=2) + "\n"


def embed_provenance(image: Blob, provenance: Provenance) -> Blob:
    """The same pixels as PNG, with the XMP packet and text fields; a lossless re-encode."""
    with Image.open(BytesIO(image.data)) as source:
        source.load()
        info = PngImagePlugin.PngInfo()
        info.add_itxt(XMP_KEY, _xmp(provenance.source_type))
        info.add_text("ai_generated", "true")
        info.add_text("digital_source_type", IPTC_SOURCE + provenance.source_type)
        info.add_text("generator", provenance.generator)
        info.add_text("source_photo_sha256", provenance.source_sha256)
        info.add_text("verification", provenance.verification)
        info.add_text("created_at", provenance.created_at.isoformat())
        out = BytesIO()
        source.save(out, format="PNG", pnginfo=info)
    return Blob(data=out.getvalue(), mime_type="image/png")


def read_provenance(image: Blob) -> dict[str, str]:
    """Text fields and XMP of a PNG, for audits and tests."""
    with Image.open(BytesIO(image.data)) as source:
        text = getattr(source, "text", {})
        return {str(key): str(value) for key, value in text.items()}


def ai_label(image: Blob, text: str = AI_LABEL) -> Blob:
    """A visible label in the bottom-left corner, sized to the image (Art. IV.3)."""
    with Image.open(BytesIO(image.data)) as source:
        canvas = source.convert("RGBA")
    width, height = canvas.size
    size = max(12, round(min(width, height) * 0.035))
    font = ImageFont.load_default(size=size)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad, margin = round(size * 0.5), round(size * 0.8)
    box_width, box_height = right - left + 2 * pad, bottom - top + 2 * pad
    x0, y0 = margin, height - margin - box_height
    draw.rounded_rectangle(
        (x0, y0, x0 + box_width, y0 + box_height), radius=round(size * 0.6), fill=(0, 0, 0, 165)
    )
    draw.text((x0 + pad - left, y0 + pad - top), text, font=font, fill=(255, 255, 255, 255))
    labelled = Image.alpha_composite(canvas, overlay).convert("RGB")
    out = BytesIO()
    labelled.save(out, format="PNG")
    return Blob(data=out.getvalue(), mime_type="image/png")


def _xmp(source_type: SourceType) -> str:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about=""'
        ' xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<Iptc4xmpExt:DigitalSourceType>{IPTC_SOURCE}{source_type}</Iptc4xmpExt:DigitalSourceType>"
        f'<dc:description><rdf:Alt><rdf:li xml:lang="es">{AI_LABEL}</rdf:li></rdf:Alt></dc:description>'
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
