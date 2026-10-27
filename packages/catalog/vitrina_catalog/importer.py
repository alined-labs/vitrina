"""Photos of menus and price lists to draft products (T021, AS-1.3, FR-003).

Spark reads the photo with a JSON schema. The checks run outside the model: every product
needs a name and exactly one whole-peso price (read with the shared money normalizer), a
repeated item counts once, and an item the catalog already has with that price is skipped.
Nothing becomes active here: the seller approves each draft before the agent can cite it
(FR-003, Art. III).
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from vitrina_catalog.schemas import CatalogStore, Product, ProductCreate
from vitrina_catalog.text import fold
from vitrina_core.jobs import JobFailedError
from vitrina_core.money import amounts_cop
from vitrina_core.providers import (
    Blob,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ContentPart,
    ImagePart,
    ResponseFormat,
    TextPart,
    with_backoff,
)

IMPORT_TASK = "catalog.import_products"
NAME_MAX = 120  # openapi.yaml ProductCreate.name
DESCRIPTION_MAX = 2000  # openapi.yaml ProductCreate.description

PROMPT = """Lees fotos de menús, listas de precios y catálogos escritos a mano de negocios pequeños en Colombia.

Devuelve cada producto que tenga un precio legible en la foto:
- name: el nombre como está escrito; corrige solo mayúsculas y tildes evidentes.
- price_cop: el precio en pesos colombianos como número entero: "$12.000", "12 mil" y "12k" son 12000.
- description: lo que la foto dice del producto (ingredientes, tamaño, presentación) o "".

REGLAS
1. Un producto sin precio legible no va. Nunca adivines ni calcules un precio.
2. Si un producto tiene varios tamaños con precios distintos, devuelve uno por tamaño, con el tamaño en el nombre.
3. Ignora teléfonos, direcciones, horarios, fechas, totales y promociones sin producto.
4. Si la foto no es un menú ni una lista de precios, devuelve una lista vacía.

Responde solo con JSON: {"products": [{"name": "...", "price_cop": 12000, "description": "..."}]}"""

# Lengths and bounds are checked below, outside the model; the schema stays plain so any
# json_schema implementation accepts it (Q01).
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["products"],
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "price_cop", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "price_cop": {"type": "integer"},
                    "description": {"type": "string"},
                },
            },
        }
    },
}

_PHOTO_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


@dataclass(frozen=True)
class DraftProduct:
    name: str
    price_cop: int
    description: str | None


def photo_type(data: bytes) -> str | None:
    """JPEG, PNG or WebP by their first bytes (FR-010); the client's content type is not trusted."""
    for signature, mime_type in _PHOTO_SIGNATURES:
        if data.startswith(signature):
            return mime_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def extract_products(
    photo: Blob, hint: str | None, *, chat: ChatModel, model: str
) -> list[DraftProduct]:
    """The priced products in the photo; one retry on an unusable answer, then a final failure."""
    content: list[ContentPart] = [TextPart(text="Extrae los productos con precio de esta foto.")]
    if hint:
        content.append(TextPart(text=f"Contexto del vendedor: {hint}"))
    content.append(ImagePart(image=photo))
    request = ChatRequest(
        model=model,
        purpose="agent",
        messages=(
            ChatMessage(role="system", content=PROMPT),
            ChatMessage(role="user", content=tuple(content)),
        ),
        response_format=ResponseFormat(name="menu_products", json_schema=SCHEMA),
        temperature=0.0,
    )
    for _attempt in range(2):
        response = await with_backoff(lambda: chat.complete(request))
        drafts = parse_products(response.message.content)
        if drafts is not None:
            break
    else:
        raise JobFailedError(
            "PROVIDER_ERROR", "No pudimos leer los productos de la foto. Inténtalo de nuevo."
        )
    if not drafts:
        raise JobFailedError(
            "INSUFFICIENT_DATA",
            "No encontramos productos con precio en la foto. Prueba con una foto más nítida.",
        )
    return drafts


def parse_products(content: object) -> list[DraftProduct] | None:
    """Valid items in reading order, repeats once; None when the answer is unusable."""
    if not isinstance(content, str):
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    items = data.get("products") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    drafts: dict[tuple[str, int], DraftProduct] = {}
    for item in items:
        draft = _draft(item)
        if draft is not None:
            drafts.setdefault((fold(draft.name), draft.price_cop), draft)
    return list(drafts.values())


def _draft(item: object) -> DraftProduct | None:
    if not isinstance(item, dict):
        return None
    name = " ".join(str(item.get("name") or "").split())
    price = _price(item.get("price_cop"))
    if not name or len(name) > NAME_MAX or price is None:
        return None
    description = " ".join(str(item.get("description") or "").split())[:DESCRIPTION_MAX]
    return DraftProduct(name=name, price_cop=price, description=description or None)


def _price(value: object) -> int | None:
    """A positive whole number of pesos: an integer, or text with exactly one amount."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        text = value.strip()
        found = [int(text)] if text.isdigit() else amounts_cop(text)
        value = found[0] if len(found) == 1 else None
    return value if isinstance(value, int) and value > 0 else None


async def save_drafts(
    catalog: CatalogStore, seller_id: UUID, drafts: list[DraftProduct]
) -> list[Product]:
    """Create each draft unless the seller already has that product at that price."""
    known: set[tuple[str, int]] = set()
    cursor: str | None = None
    while True:
        page, cursor = await catalog.list_products(seller_id, limit=100, cursor=cursor)
        known.update(
            (fold(item.name), item.price_cop) for item in page if item.status != "archived"
        )
        if cursor is None:
            break
    created = []
    for draft in drafts:
        if (fold(draft.name), draft.price_cop) in known:
            continue
        fields: dict[str, Any] = {
            "name": draft.name,
            "price_cop": draft.price_cop,
            "status": "draft",
        }
        if draft.description:
            fields["description"] = draft.description
        product = await catalog.create_product(
            seller_id, ProductCreate.model_validate(fields), source="image_import"
        )
        created.append(product)
        known.add((fold(draft.name), draft.price_cop))
    return created
