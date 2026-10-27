"""Copywrite stage (T040): one Spark call writes the hook × angle copy grid for a product.

AS-2.4, plan.md §4.2 step 4, brain ``prompts/copywriter.md``. The engine plans the grid (every
angle at least once, no hook and angle pair twice before all are used) and Spark writes one text
per cell. Each text is checked outside the model: at most 300 characters, the ``{wa_link}``
placeholder exactly once, no amount other than the product price or one in the seller's facts
(the same normalizer as the price validator, R2), and no testimonials (constitution Art. IV.2).
Failing cells are rewritten once with their problems listed; cells still failing are dropped
and reported. Truthfulness also shapes the grid: ``scarcity`` needs a low, known stock, and
``handmade`` needs the product data to say so (Ley 1480). Brand-voice scoring is T041.
"""

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, get_args

from pydantic import BaseModel

from vitrina_core.jobs import JobFailedError
from vitrina_core.money import amounts_cop, format_cop
from vitrina_core.providers import ChatMessage, ChatModel, ChatRequest, ResponseFormat, with_backoff

CopyHook = Literal["question", "bold_claim", "story", "social_proof_free", "scarcity", "benefit"]
CopyAngle = Literal["price", "quality", "handmade", "urgency", "gift", "local_pride"]
HOOKS: tuple[CopyHook, ...] = get_args(CopyHook)
ANGLES: tuple[CopyAngle, ...] = get_args(CopyAngle)

WA_LINK = "{wa_link}"
MAX_CHARS = 300
LOW_STOCK = 5
HANDMADE_WORDS = re.compile(r"hecho a mano|hecha a mano|artesan|handmade|tejid|bordad|a mano")
TESTIMONIAL = re.compile(
    r"\b(clientes?|compradores?|personas?)\s+(dicen|opinan|aman|recomiendan|felices|satisfech)"
    r"|\bresen(a|as)\b|\btestimonio|\bopiniones\b|\b\d\s*estrellas\b|[★⭐]"
    r"|\bantes\s+y\s+despues\b|\bmiles\s+de\s+(clientes|personas|ventas)\b|\bmas\s+vendid"
    r'|["“][^"”]{3,90}["”]\s*[-—–]\s*\w'
)


class BrandVoice(BaseModel):
    """openapi.yaml ``BrandVoiceProfile``; defaults apply before any voice sample is imported."""

    formality: float = 3.0
    warmth: float = 4.0
    emoji_rate: float = 0.2
    regionalisms: tuple[str, ...] = ()


@dataclass(frozen=True)
class CopyInput:
    business_name: str
    city: str
    product_name: str
    product_description: str
    # None when no price is published (a public showcase may omit it): then no amount at all.
    price_cop: int | None
    stock: int | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    # Active seller facts (shipping, payment, policies): the only other claims and amounts allowed.
    facts: tuple[str, ...] = ()
    voice: BrandVoice = field(default_factory=BrandVoice)
    scene_brief: str | None = None
    # None: decide from the description and attributes.
    handmade: bool | None = None


class CopyDraft(BaseModel):
    hook: CopyHook
    angle: CopyAngle
    text: str


@dataclass(frozen=True)
class DroppedCell:
    hook: CopyHook
    angle: CopyAngle
    problems: tuple[str, ...]


@dataclass(frozen=True)
class CopyResult:
    variants: tuple[CopyDraft, ...]
    dropped: tuple[DroppedCell, ...]


class CopyError(JobFailedError):
    """No usable copy at all: a final job failure."""


def is_handmade(item: CopyInput) -> bool:
    if item.handmade is not None:
        return item.handmade
    text = " ".join([item.product_name, item.product_description, *item.attributes.values()])
    return bool(HANDMADE_WORDS.search(_fold(text)))


def plan_grid(
    count: int,
    angles: Sequence[CopyAngle] | None = None,
    *,
    allow_scarcity: bool = True,
    allow_handmade: bool = True,
    allow_price: bool = True,
) -> list[tuple[CopyHook, CopyAngle]]:
    """``count`` cells: angles in turn, the hook shifted each lap, so no pair repeats early."""
    pool = [
        a
        for a in (angles or ANGLES)
        if (allow_handmade or a != "handmade") and (allow_price or a != "price")
    ]
    hooks = [h for h in HOOKS if allow_scarcity or h != "scarcity"]
    if not pool:
        raise ValueError("no angle left for this product")
    cells: list[tuple[CopyHook, CopyAngle]] = []
    lap = 0
    while len(cells) < count:
        for index, angle in enumerate(pool):
            if len(cells) == count:
                break
            cells.append((hooks[(index + lap) % len(hooks)], angle))
        lap += 1
    return cells


def allowed_amounts(item: CopyInput) -> set[int]:
    """The product price, when published; the facts' amounts are added by the caller."""
    return {item.price_cop} if item.price_cop is not None else set()


def price_rule(item: CopyInput) -> str:
    if item.price_cop is None:
        return (
            "Este producto no tiene precio publicado: no escribas ningún precio, monto, "
            'descuento, "antes/ahora" ni porcentaje, salvo los que aparecen en los datos del negocio.'
        )
    return (
        f"El único precio del producto es {format_cop(item.price_cop)}, escrito exactamente así. "
        'Ningún otro monto, descuento, "antes/ahora" ni porcentaje, salvo los que aparecen en '
        "los datos del negocio."
    )


def check_copy(text: str, allowed_amounts: set[int]) -> list[str]:
    """The problems of one text, in Spanish (they go back to the model in the rewrite)."""
    problems: list[str] = []
    if len(text) > MAX_CHARS:
        problems.append(f"tiene {len(text)} caracteres y el máximo es {MAX_CHARS}")
    if text.count(WA_LINK) != 1:
        problems.append(f"debe incluir {WA_LINK} exactamente una vez")
    extra = sorted(set(amounts_cop(text)) - allowed_amounts)
    if extra:
        problems.append(
            "menciona montos que no están en los datos: " + ", ".join(map(format_cop, extra))
        )
    if TESTIMONIAL.search(_fold(text)):
        problems.append("parece un testimonio, una reseña o una cifra de ventas; no se permiten")
    return problems


async def write_copy(
    item: CopyInput,
    *,
    count: int = 10,
    angles: Sequence[CopyAngle] | None = None,
    writer: ChatModel,
    model: str,
) -> CopyResult:
    """The grid for one product: ``count`` checked texts, or a CopyError when none survive."""
    cells = plan_grid(
        count,
        angles,
        allow_scarcity=item.stock is not None and 0 < item.stock <= LOW_STOCK,
        allow_handmade=is_handmade(item),
        allow_price=item.price_cop is not None,
    )
    allowed = allowed_amounts(item)
    for fact in item.facts:
        allowed.update(amounts_cop(fact))

    texts = await _write(item, cells, list(range(len(cells))), {}, writer=writer, model=model)
    problems = {slot: check_copy(texts.get(slot, ""), allowed) for slot in range(len(cells))}
    failing = [slot for slot, found in problems.items() if found]
    if failing:  # one rewrite of the failing cells, each with its problems
        retry = await _write(item, cells, failing, problems, writer=writer, model=model)
        for slot in failing:
            if slot in retry:
                texts[slot] = retry[slot]
                problems[slot] = check_copy(retry[slot], allowed)

    variants = tuple(
        CopyDraft(hook=cells[slot][0], angle=cells[slot][1], text=texts[slot])
        for slot in range(len(cells))
        if not problems[slot]
    )
    dropped = tuple(
        DroppedCell(hook=cells[slot][0], angle=cells[slot][1], problems=tuple(problems[slot]))
        for slot in range(len(cells))
        if problems[slot]
    )
    if not variants:
        raise CopyError("COPY_FAILED", "No pudimos escribir textos válidos para este producto.")
    return CopyResult(variants=variants, dropped=dropped)


PROMPT = """Eres quien escribe los anuncios de {business}, en {city}. Escribes textos cortos para WhatsApp e Instagram en español de Colombia, con la voz del negocio y solo con datos reales.

REGLAS
1. Escribe un texto por casilla, con el gancho y el ángulo que la casilla indica.
2. Máximo 300 caracteres por texto, contando el marcador {{wa_link}}, que va una sola vez, al final, tal cual.
3. {price_rule}
4. Nada de testimonios, reseñas, clientes inventados, cifras de ventas, "el más vendido", "antes y después" ni promesas que no estén en los datos. social_proof_free significa persuadir sin apelar a lo que otros dicen o compran.
5. urgency no inventa fechas, plazos ni existencias. scarcity dice el stock tal cual.
6. Envíos, pagos y políticas solo como aparecen en los datos del negocio.
7. Sin markdown, sin hashtags, sin mayúsculas sostenidas. Emojis según la voz del negocio.

GANCHOS: question (pregunta), bold_claim (afirmación fuerte y verdadera), story (mini historia), social_proof_free (persuasión sin prueba social), scarcity (pocas unidades), benefit (beneficio concreto).
ÁNGULOS: price (precio), quality (calidad), handmade (hecho a mano), urgency (por qué ahora), gift (para regalar), local_pride (orgullo local).

Responde solo con JSON y nada más: {{"variants": [{{"slot": 1, "text": "..."}}]}}"""


async def rewrite_cells(
    item: CopyInput,
    cells: Sequence[tuple[CopyHook, CopyAngle]],
    problems: Sequence[Sequence[str]],
    *,
    writer: ChatModel,
    model: str,
) -> dict[int, str]:
    """Rewrite given cells once, each with its problems listed; texts by cell index."""
    return await _write(
        item,
        list(cells),
        list(range(len(cells))),
        {index: list(found) for index, found in enumerate(problems)},
        writer=writer,
        model=model,
    )


async def _write(
    item: CopyInput,
    cells: list[tuple[CopyHook, CopyAngle]],
    slots: list[int],
    problems: dict[int, list[str]],
    *,
    writer: ChatModel,
    model: str,
) -> dict[int, str]:
    """One Spark call for ``slots``; returns the texts it produced, by slot."""
    voice = item.voice
    price = format_cop(item.price_cop) if item.price_cop is not None else "sin publicar"
    lines = [
        f"PRODUCTO: {item.product_name}. {item.product_description}".strip(),
        f"Precio: {price}" + (f" · Stock: {item.stock} unidades" if item.stock is not None else ""),
    ]
    lines += [f"Atributo {key}: {value}" for key, value in item.attributes.items()]
    lines.append("DATOS DEL NEGOCIO:" if item.facts else "DATOS DEL NEGOCIO: ninguno")
    lines += [f"- {fact}" for fact in item.facts]
    lines.append(
        f"VOZ: formalidad {voice.formality:g}/5, calidez {voice.warmth:g}/5, "
        f"emojis {voice.emoji_rate:g} por texto, regionalismos: {', '.join(voice.regionalisms) or 'ninguno'}"
    )
    if item.scene_brief:
        lines.append(f"DIRECCIÓN DEL BRIEF: {item.scene_brief}")
    lines.append("CASILLAS:")
    for slot in slots:
        hook, angle = cells[slot]
        line = f"{slot + 1}. hook={hook}, angle={angle}"
        if problems.get(slot):
            line += " · corrige: " + "; ".join(problems[slot])
        lines.append(line)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["variants"],
        "properties": {
            "variants": {
                "type": "array",
                "minItems": len(slots),
                "maxItems": len(slots),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["slot", "text"],
                    "properties": {
                        "slot": {"type": "integer"},
                        "text": {"type": "string", "maxLength": MAX_CHARS},
                    },
                },
            }
        },
    }
    request = ChatRequest(
        model=model,
        purpose="copy",
        messages=(
            ChatMessage(
                role="system",
                content=PROMPT.format(
                    business=item.business_name, city=item.city, price_rule=price_rule(item)
                ),
            ),
            ChatMessage(role="user", content="\n".join(lines)),
        ),
        response_format=ResponseFormat(name="copy_grid", json_schema=schema),
        temperature=0.7,
    )
    response = await with_backoff(lambda: writer.complete(request))
    return _parse_texts(response.message.content, {slot + 1 for slot in slots})


def _parse_texts(content: object, wanted: set[int]) -> dict[int, str]:
    if not isinstance(content, str):
        return {}
    start, end = content.find("{"), content.rfind("}")
    try:
        data = json.loads(content[start : end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        return {}
    items = data.get("variants") if isinstance(data, dict) else None
    texts: dict[int, str] = {}
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict):
            continue
        slot, text = entry.get("slot"), entry.get("text")
        if (
            isinstance(slot, int)
            and slot in wanted
            and isinstance(text, str)
            and slot - 1 not in texts
        ):
            texts[slot - 1] = text.strip()
    return texts


def _fold(text: str) -> str:
    """Lower case without accents, for keyword checks."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
