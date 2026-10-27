"""Inbound messages in the canonical form the agent consumes (FR-022; openapi ``NormalizedMessage``).

Text, the title of a tapped reply and a media caption all become ``text``. The keyword
checks run here, before anything else sees the message (R7, R8; FR-063): opt-out, erasure,
and an affirmative answer to a pending consent prompt, with the compliance module's own
matchers. Images and voice notes keep their kind; cropping, description and transcription
arrive with T050 and T051. Video, documents and everything else are ``unsupported``.
"""

from typing import Literal
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

from vitrina_compliance.api import is_affirmative, is_erasure_request, is_opt_out_request
from vitrina_conversation.attribution import parse_ad_code

NormalizedKind = Literal["text", "image", "audio", "interactive", "unsupported"]
KINDS: dict[str, NormalizedKind] = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "interactive": "interactive",
}
LANGUAGE = "es"  # es-CO only (NFR-08); detection arrives with transcription (T051)


class NormalizedImage(BaseModel):
    crop_url: str | MISSING = Field(default=MISSING, json_schema_extra={"format": "uri"})
    description: str | MISSING = MISSING
    matched_product_ids: list[UUID] | MISSING = MISSING


class NormalizedMessage(BaseModel):
    """openapi.yaml ``NormalizedMessage``: every property optional."""

    kind: NormalizedKind | MISSING = MISSING
    text: str | MISSING = MISSING
    language: str | MISSING = MISSING
    image: NormalizedImage | MISSING = MISSING
    ad_code: str | None = None
    opt_out_detected: bool | MISSING = MISSING
    erasure_requested: bool | MISSING = MISSING
    consent_affirmative: bool | MISSING = MISSING


def normalize(
    kind: str, text: str | None, *, pending_consent_prompt: str | None = None
) -> NormalizedMessage:
    """``kind`` is the message type as received (``MessageType``); ``text`` its words, if any."""
    words = text or ""
    return NormalizedMessage(
        kind=KINDS.get(kind, "unsupported"),
        text=words,
        language=LANGUAGE,
        ad_code=parse_ad_code(words),
        opt_out_detected=is_opt_out_request(words),
        erasure_requested=is_erasure_request(words),
        consent_affirmative=pending_consent_prompt is not None and is_affirmative(words),
    )
