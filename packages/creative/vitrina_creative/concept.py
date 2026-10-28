"""What SAM is asked to find: a short English noun for the seller's Spanish product name.

SAM 3.1 takes a concept as a short noun phrase and, live, finds nothing for Spanish ones:
"bag", "handbag" and "woven bag" all found a Wayuu mochila (0.93–0.96), while "mochila", "bolso"
and "Mochila wayuu" found nothing (research/findings/2026-10-28-muse-image-live.md). So one
small Spark call names the product in English before segmenting; the seller never sees it.
"""

import json
import re

from vitrina_core.providers import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ProviderError,
    ResponseFormat,
    with_backoff,
)

FALLBACK = "product"
MAX_WORDS = 3

PROMPT = (
    "You name products for an image segmentation model that only understands short English "
    "noun phrases. Given a product from a Colombian shop (name and description in Spanish), "
    "answer with the physical object a camera would see, in English, 1 to 3 words, lowercase, "
    'no brand, no colour unless needed: "Mochila wayuu" → "woven bag", "Arepa de queso" → '
    '"arepa", "Camiseta Selección" → "t-shirt".'
)

SCHEMA = {
    "type": "object",
    "properties": {"noun": {"type": "string"}},
    "required": ["noun"],
    "additionalProperties": False,
}


async def sam_concept(name: str, description: str, *, chat: ChatModel, model: str) -> str:
    """The English noun phrase for SAM; ``FALLBACK`` when Spark gives nothing usable."""
    request = ChatRequest(
        model=model,
        purpose="segment",
        messages=(
            ChatMessage(role="system", content=PROMPT),
            ChatMessage(role="user", content=f"Name: {name}\nDescription: {description}"),
        ),
        response_format=ResponseFormat(name="sam_concept", json_schema=SCHEMA),
        temperature=0.0,
    )
    try:
        response = await with_backoff(lambda: chat.complete(request))
    except ProviderError:
        return FALLBACK
    content = response.message.content
    return clean(content if isinstance(content, str) else "")


def clean(content: str) -> str:
    """The noun from Spark's JSON (or bare text), cut to a few plain words."""
    try:
        data = json.loads(content)
        text = data.get("noun", "") if isinstance(data, dict) else ""
    except json.JSONDecodeError:
        text = content
    words = re.sub(r"[^a-z\- ]", " ", str(text).lower()).split()
    return " ".join(words[:MAX_WORDS]) or FALLBACK
