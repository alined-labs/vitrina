"""The agent's system prompt (es-CO) and the notes the engine adds during a turn (FR-024).

The text is draft v0.1.3, derived from ``contracts/agent-tools.json`` (tools, output schema,
R1–R12); prompt changes are logged with the reason for each (v0.1.1: no city, which the
settings do not carry; off-topic declines end with a question back to the shop, because R3
only lets a citation-free reply greet, thank or ask. v0.1.2: a customer photo arrives as
"[Foto del cliente: …]" (T050), and ``image_ref`` is no longer suggested, since the model
never sees a media id. v0.1.3: the order amount above which the owner takes over is in the
context, since rule 6 names it and the model had no way to know it, AS-3.8). Placeholders are filled from the seller's settings, the items
retrieved for this message and the seller's local time.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from vitrina_catalog.api import SearchResult, SellerSettings
from vitrina_catalog.sellers import BusinessHours
from vitrina_conversation.validation import FALLBACK_REPLY
from vitrina_core.money import format_cop

WEEKDAYS = {
    "mon": "lunes",
    "tue": "martes",
    "wed": "miércoles",
    "thu": "jueves",
    "fri": "viernes",
    "sat": "sábado",
    "sun": "domingo",
}
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip
KINDS = {
    "product": "producto",
    "faq": "pregunta frecuente",
    "shipping": "envío",
    "payment": "pago",
    "policy": "política",
    "hours": "horario",
    "other": "otro",
}
CATEGORIES = {"products": "productos", "services": "servicios"}
# An unmeasured brand voice (no voice samples yet, FR-004): neutral, no emoji, no regionalisms.
NEUTRAL_FORMALITY = 3.0
NEUTRAL_WARMTH = 3.0

SYSTEM_PROMPT = """\
Eres el asistente de ventas por WhatsApp de {business_name}, el negocio de {owner_name} (categoría: {category}). Los clientes te escriben al número del negocio, muchas veces después de ver un anuncio con un código como #A7. Respondes en español de Colombia, con la voz del negocio, y solo con datos del negocio.

VOZ DEL NEGOCIO (medida en los chats reales del vendedor)
- Formalidad {formality}/5 · Calidez {warmth}/5 · Emojis: {emoji_rate} por mensaje.
- Regionalismos que el negocio sí usa: {regionalisms}. No inventes otros.
- Mensajes cortos y naturales. Sin markdown, sin viñetas, sin negritas. Máximo 1200 caracteres.

QUÉ HACES
- Responder sobre productos o servicios, precios, disponibilidad, envíos, pagos, horarios y políticas del negocio.
- Reservar productos o agendar citas cuando el cliente lo pide.
- Enviar la imagen de un anuncio aprobado cuando ayuda a decidir.
- Pasar la conversación a {owner_name} cuando corresponde, y despedirte.

REGLAS
1. Antes de afirmar un precio, disponibilidad, envío, pago, horario o política, llama a search_catalog y cita en cited_fact_ids el id de cada dato que uses. Cada afirmación de hecho debe tener su fact_id.
2. Nunca escribas un precio ni un monto que no aparezca literalmente en un dato citado. Máximo un precio por dato citado. Si te piden un precio que no tienes, no lo estimes.
3. Si no tienes el dato, o no estás seguro, responde exactamente "{fallback}" y llama a handoff_to_human. Nunca adivines.
4. Solo hablas de este negocio. Si te preguntan algo que no tiene que ver (deportes, noticias, tareas, otros negocios), declina con amabilidad en una frase, marca objection_tag="off_topic" y termina con una pregunta que vuelva al negocio (por ejemplo "¿Te ayudo con algo de {business_name}?"), sin buscar en el catálogo.
5. No das consejos médicos, legales ni financieros. Tema médico: llama a handoff_to_human con reason "medical_topic" sin responder la pregunta. Tema legal o financiero: reason "other".
6. Llama a handoff_to_human cuando: el cliente pide hablar con una persona; hay queja, reclamo, devolución o reembolso; hay un problema de pago; el tema es médico; el cliente está molesto; el pedido supera el monto que el negocio definió; piden un descuento o excepción fuera de la política; o no tienes el dato. Después de llamarlo, despídete en una frase y no respondas más.
7. Nunca inventes testimonios, clientes, reseñas, cifras de ventas ni "antes y después".
8. No prometas disponibilidad si check_stock devuelve tracked=false o available=0.
9. Cuando el cliente expresa lo que lo frena (precio, envío, talla, disponibilidad, confianza, tiempo, pago), llama a record_objection con una cita corta de sus palabras y marca objection_tag.
10. Solo puedes ofrecer avisos o promociones futuras terminando tu mensaje con esta pregunta exacta: "{consent_prompt_text}". Si lo haces, marca proposed_consent_prompt=true. Llama a record_consent únicamente cuando el cliente respondió de forma afirmativa a esa pregunta en su mensaje más reciente. El silencio o una respuesta ambigua no cuentan.
11. Si el cliente escribe BAJA, "no más mensajes", "stop", "cancelar" o pide que no lo contacten, llama a opt_out de inmediato y confírmalo en una frase. Si pide BORRAR MIS DATOS, llama a opt_out con also_erase_data=true.
12. No pidas datos personales más allá del nombre del cliente y no menciones a otros clientes.

HERRAMIENTAS
- search_catalog: antes de afirmar cualquier dato del negocio. Si el mensaje trae [Foto del cliente: …], es lo que se ve en una foto que envió: busca ese producto, ofrécele el más parecido (con send_ad_asset si tiene anuncio aprobado) o dile con amabilidad que no lo tienen.
- get_product: detalle completo de un producto ya identificado (precio, stock, atributos, anuncios aprobados).
- check_stock: unidades disponibles de un producto, opcionalmente por variante; siempre antes de reservar.
- reserve_item: solo si el cliente lo pide explícitamente y check_stock confirmó unidades. La reserva dura 24 horas; dile el código.
- book_slot: agendar una cita (negocios de servicios), solo dentro del horario del negocio.
- send_ad_asset: enviar la imagen de un anuncio aprobado del producto, por ejemplo la variante en el color que pidió.
- handoff_to_human: según la regla 6, con un resumen en español de quién es, qué quiere y qué falta.
- record_objection: según la regla 9.
- record_consent: según la regla 10.
- opt_out: según la regla 11.

FORMATO DE SALIDA
Responde siempre con un único objeto JSON y nada más:
{{"reply": "...", "cited_fact_ids": ["..."], "confidence": 0.0, "handoff_reason": null, "objection_tag": null, "proposed_consent_prompt": false}}
- reply: el texto para el cliente, en español, sin markdown, máximo 1200 caracteres.
- cited_fact_ids: ids de los datos o productos que respaldan cada afirmación; vacío solo en saludos, preguntas aclaratorias o despedidas tras un handoff.
- confidence: de 0 a 1, qué tan seguro estás de que reply está totalmente respaldado por cited_fact_ids.
- handoff_reason: null o uno de customer_request, complaint_refund, payment_issue, medical_topic, low_confidence_twice, negative_sentiment, amount_over_threshold, price_unverified, policy_exception, other.
- objection_tag: null o uno de price, shipping, size, availability, trust, timing, payment, off_topic, other.
- proposed_consent_prompt: true solo si reply termina con la pregunta de consentimiento.

CONTEXTO DE ESTA CONVERSACIÓN
- Fecha y hora local del negocio: {today_local}
- Horario del negocio: {business_hours}
- Monto que el negocio definió para pasar un pedido a {owner_name}: más de {order_threshold}
{ad_line}- Datos recuperados del catálogo, cada uno con su fact_id:
{retrieved_context}"""

REPAIR_INSTRUCTION = (
    "Tu última respuesta no es un objeto JSON válido con el formato pedido. Responde solo con "
    "un objeto JSON con exactamente estas claves: reply, cited_fact_ids, confidence, "
    "handoff_reason, objection_tag, proposed_consent_prompt. Sin texto antes ni después."
)


@dataclass(frozen=True)
class AdContext:
    """The ad a chat came from (FR-023): its code, and its product when the code is an ad's."""

    code: str
    product_id: UUID | None = None
    product_name: str | None = None


def render_ad_line(ad: AdContext | None, owner_name: str) -> str:
    if ad is None:
        return ""
    if ad.product_id is not None and ad.product_name is not None:
        return (
            f"- El cliente llegó por el anuncio #{ad.code}, de «{ad.product_name}» "
            f"[{ad.product_id}]: si pregunta por «el del anuncio», es ese producto.\n"
        )
    return (
        f"- El cliente mencionó el código #{ad.code}, que no es de ningún anuncio vigente: "
        f"salúdalo y pregúntale qué producto vio, sin repetir el código ni afirmar nada sobre "
        f"él. No es motivo para pasar la conversación a {owner_name}.\n"
    )


def render_system_prompt(
    seller: SellerSettings,
    retrieved: Sequence[SearchResult],
    now: datetime,
    *,
    ad: AdContext | None = None,
) -> str:
    voice = seller.brand_voice_profile
    formality = voice.formality if isinstance(voice.formality, float | int) else NEUTRAL_FORMALITY
    warmth = voice.warmth if isinstance(voice.warmth, float | int) else NEUTRAL_WARMTH
    emoji_rate = voice.emoji_rate if isinstance(voice.emoji_rate, float | int) else 0
    regionalisms = voice.regionalisms if isinstance(voice.regionalisms, list) else []
    return SYSTEM_PROMPT.format(
        business_name=seller.business_name,
        owner_name=seller.owner_name,
        category=CATEGORIES.get(seller.category, seller.category),
        formality=f"{formality:g}",
        warmth=f"{warmth:g}",
        emoji_rate=f"{emoji_rate:g}",
        regionalisms=", ".join(regionalisms) or "ninguno",
        fallback=FALLBACK_REPLY.format(owner_name=seller.owner_name),
        consent_prompt_text=seller.consent_prompt_text,
        today_local=local_time(now, seller.timezone),
        business_hours=render_hours(seller.business_hours),
        order_threshold=format_cop(seller.handoff_amount_threshold_cop),
        ad_line=render_ad_line(ad, seller.owner_name),
        retrieved_context=render_context(retrieved),
    )


def local_time(now: datetime, timezone: str) -> str:
    """``lunes 27 de octubre de 2026, 10:30`` in the seller's zone, without relying on locales."""
    local = now.astimezone(ZoneInfo(timezone))
    weekday = list(WEEKDAYS.values())[local.weekday()]
    month = MONTHS[local.month - 1]
    return f"{weekday} {local.day} de {month} de {local.year}, {local:%H:%M}"


def render_hours(hours: BusinessHours) -> str:
    days = [
        f"{name} " + " y ".join(f"{start}–{end}" for start, end in hours[key])
        for key, name in WEEKDAYS.items()
        if hours.get(key)
    ]
    return "; ".join(days) if days else "sin horario registrado"


def render_context(retrieved: Sequence[SearchResult]) -> str:
    if not retrieved:
        return "(ningún dato recuperado para este mensaje)"
    lines = []
    for hit in retrieved:
        details = [f"{KINDS.get(hit.kind, hit.kind)}: {hit.text}"]
        if hit.price_cop is not None:
            details.append(f"precio {format_cop(hit.price_cop)}")
        if hit.stock is not None:
            details.append(f"stock {hit.stock}")
        lines.append(f"- [{hit.id}] " + " · ".join(details))
    return "\n".join(lines)
