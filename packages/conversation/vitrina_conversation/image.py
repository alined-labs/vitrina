"""Customer photos (T050, AS-3.5): what the photo shows, in words the catalog can match.

The photo is downloaded by its media id, Spark (vision) describes its main product in one
short Spanish sentence, and the seller's active products are searched with that description.
The description reaches the agent with the customer's caption, so the agent searches, offers
the closest product (with ``send_ad_asset`` when it has an approved ad) or says it is not in
the catalog. Cropping with SAM before the description waits on a live segmenter (Q01), and
an image-embedding search on an embedding model (Q06); until then the match is by words.
"""

import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from vitrina_catalog.api import Catalog
from vitrina_core.providers import (
    Blob,
    CallMeta,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ImagePart,
    TextPart,
)

logger = logging.getLogger(__name__)

NO_PRODUCT = "sin producto"
QUERY_LENGTH = 300  # search_catalog's query maxLength (agent-tools.json)
MATCHES = 3

DESCRIBE_PROMPT = f"""\
Eres los ojos de una tienda que vende por WhatsApp. Un cliente envió esta foto.
Describe en una sola frase corta, en español, el producto principal que se ve: qué es, color, \
material, estampado y cualquier texto o marca visible.
Solo lo que se ve. No inventes precios, tallas, disponibilidad ni nada que no esté en la foto.
Si no hay un producto claro, responde exactamente "{NO_PRODUCT}"."""

MediaDownload = Callable[[str], Awaitable[Blob]]


class PhotoReader(Protocol):
    """Customer photos to words, for the pipeline."""

    async def read(self, seller_id: UUID, media_id: str) -> PhotoReading: ...


@dataclass(frozen=True)
class PhotoReading:
    description: str | None
    """None when the photo shows no clear product."""
    matched_product_ids: tuple[UUID, ...] = ()
    calls: tuple[CallMeta, ...] = ()


@dataclass(frozen=True)
class Photos:
    download: MediaDownload
    chat: ChatModel
    model: str
    """``MODEL_AGENT``: Spark reads images (settings.model_id("agent"); never a literal, Q01)."""
    catalog: Catalog

    async def read(self, seller_id: UUID, media_id: str) -> PhotoReading:
        """``ProviderError`` or ``TimeoutError`` when the photo cannot be fetched or read."""
        photo = await self.download(media_id)
        response = await self.chat.complete(
            ChatRequest(
                model=self.model,
                purpose="agent",
                messages=(
                    ChatMessage(role="system", content=DESCRIBE_PROMPT),
                    ChatMessage(
                        role="user",
                        content=(TextPart(text="Foto del cliente:"), ImagePart(image=photo)),
                    ),
                ),
                temperature=0.0,
            )
        )
        content = response.message.content
        words = " ".join(content.split()) if isinstance(content, str) else ""
        if not words or _folded(words) == NO_PRODUCT:
            return PhotoReading(None, calls=(response.meta,))
        description = words[:QUERY_LENGTH]
        hits = await self.catalog.search(seller_id, description, kinds=("product",), top_k=MATCHES)
        return PhotoReading(description, tuple(hit.id for hit in hits), (response.meta,))


def _folded(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", letters))
