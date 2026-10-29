"""The checks that run outside the model on every answer (R2–R5; FR-026; ADR-0006).

``review`` judges one ``AgentTurnOutput`` against the active items it cites and decides what
the customer gets:

- R2 price: every amount in the reply (``vitrina_core.money``, the normalizer that filled the
  catalog's ``amounts_cop``) must be an amount of a cited item. A violation earns one guided
  retry; a second one gets the fallback and a ``price_unverified`` handoff (AS-3.4).
- R3 groundedness: every cited id must be an active item of this seller; one unknown id is
  enough to count the answer as confidence 0. A reply without citations may only greet,
  thank, ask the customer something, decline an off-topic request or say the fallback
  sentence; anything else is an uncited claim and counts as confidence 0. Handoff replies
  need no citation (agent-tools.json ``cited_fact_ids``).
- R4 confidence: below the seller's threshold (default 0.6) the fallback replaces the reply
  and the turn counts as low confidence. The fallback tells the customer the owner will
  answer, so it hands the chat to them (reason ``other``; Art. III.3, amendment A-010), and
  a second in a row, after the owner hands the chat back, is ``low_confidence_twice``.
- R5: a handoff the model chose keeps its reason and its reply (still under R2).
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from vitrina_catalog.api import Citation
from vitrina_conversation.contract import AgentTurnOutput, HandoffReason
from vitrina_core.money import amounts_cop, format_cop

FALLBACK_REPLY = "No tengo ese dato, te comunico con {owner_name}"
LOW_CONFIDENCE_THRESHOLD = 0.6  # data-model.md sellers.low_confidence_threshold default

Outcome = Literal["accept", "retry", "fallback"]

# Words a citation-free reply may use outside its questions (R3): greetings, thanks, closings
# ("quedo atenta si necesitas algo más") and Colombian forms of address; none states a fact.
# Folded: lower case, no accents.
COURTESY = frozenset({
    "a", "al", "algo", "alguna", "alguno", "amiga", "amigo", "aqui", "atenderte", "atenta",
    "atento", "ayudarte", "bien", "bienvenida", "bienvenidas", "bienvenido", "bienvenidos", "buen",
    "buena", "buenas", "bueno", "buenos", "claro", "con", "cualquier", "cuando", "dale", "de",
    "decidas", "decides", "del", "dia", "dias", "don", "dona", "duda", "dudas", "e", "el",
    "encantada", "encantado", "escribe", "escribenos", "escribes", "escribir", "escribirnos",
    "estamos", "excelente", "favor", "feliz", "genial", "gracias", "gusto", "habla", "hola",
    "holi", "holis", "la", "las", "le", "listo", "lo", "los", "mas", "mi", "mija", "mijo", "mil",
    "muchas", "muchisimas", "mucho", "necesitas", "necesites", "noche", "noches", "nos", "o", "ok",
    "okay", "orden", "ordenes", "para", "parce", "parcera", "parcero", "pendiente", "perfecto",
    "placer", "por", "porfa", "que", "quedo", "querida", "querido", "quieras", "quieres", "reina",
    "saluda", "saludamos", "saludo", "saludos", "seguimos", "senor", "senora", "servirle",
    "servirte", "si", "super", "tal", "tarde", "tardes", "te", "tengas", "tienes", "tu", "tus",
    "un", "una", "vale", "veci", "vecina", "vecino", "vecinos", "y",
})  # fmt: skip
# Inside a question to the customer only these may appear besides courtesy words and names:
# question words, the customer's wishes and neutral nouns. "gratis", "hay", "llega" or
# "descuento" inside a question still claim something (review of #51).
QUESTION_WORDS = frozenset({
    "a", "al", "algo", "algun", "alguna", "alguno", "anuncio", "anuncios", "ayudamos", "ayudar",
    "ayudo", "busca", "buscabas", "buscan", "buscas", "cantidad", "ciudad", "color", "colores",
    "como", "compra", "comprar", "con", "cosa", "cual", "cuales", "cuando", "cuanta", "cuantas",
    "cuanto", "cuantos", "de", "del", "detalles", "diseno", "disponibilidad", "donde", "el", "en",
    "envio", "es", "estilo", "gustaria", "informacion", "interesa", "interesan", "la", "las", "le",
    "les", "lo", "los", "mas", "me", "medida", "medidas", "modelo", "modelos", "necesita",
    "necesitas", "nombre", "o", "ocasion", "otra", "otras", "otro", "otros", "para", "pedido",
    "podemos", "por", "precio", "precios", "prefiere", "prefieres", "producto", "productos",
    "puedo", "que", "quien", "quienes", "quiere", "quieren", "quieres", "referencia", "regalo",
    "saber", "sera", "seria", "sirve", "sirvo", "su", "sus", "talla", "tallas", "tamano", "te",
    "tu", "tus", "un", "una", "vio", "viste", "y",
})  # fmt: skip
# A reply holding one of these answers the customer's question, so it needs a citation.
ANSWER_WORDS = frozenset({
    "claro", "correcto", "dale", "exacto", "listo", "no", "obvio", "ok", "okay", "perfecto",
    "seguro", "si", "supuesto", "vale",
})  # fmt: skip
# How a customer's message reads as a question when it carries no "?".
ASKING = frozenset({
    "aceptan", "como", "cual", "cuales", "cuando", "cuanto", "cuanta", "cuantos", "cuantas",
    "donde", "envian", "hacen", "hay", "manejan", "me", "pueden", "puedo", "que", "quien",
    "reciben", "sirve", "tiene", "tienen", "venden",
})  # fmt: skip
# A question to the customer: "¿…?", or a sentence ending in "?" written without "¿".
_QUESTION = re.compile(r"¿[^?]*\?|[^.!?¡¿\n]*\?")
# A sentence that opens with one of these asks the customer something ("Cuéntame qué buscas.").
REQUESTS = frozenset({
    "avisame", "comentame", "contame", "cuentame", "cuentanos", "dime", "dinos", "escribeme",
    "escribenos", "indicame", "mandame", "regalame",
})  # fmt: skip
_SENTENCE = re.compile(r"[^.!\n]+")


@dataclass(frozen=True)
class PriceCheck:
    passed: bool
    amounts_in_reply: tuple[int, ...]
    amounts_in_citations: tuple[int, ...]
    violations: tuple[int, ...]


@dataclass(frozen=True)
class GroundednessCheck:
    passed: bool
    uncited_claims: tuple[str, ...]


@dataclass(frozen=True)
class Review:
    """What the customer gets for one answer; ``final_reply`` is None when a retry is due."""

    outcome: Outcome
    final_reply: str | None
    handoff_reason: HandoffReason | None
    low_confidence_streak: int
    confidence: float
    price: PriceCheck
    groundedness: GroundednessCheck
    retried: bool


def check_prices(reply: str, citations: Sequence[Citation]) -> PriceCheck:
    """R2: the amounts in ``reply`` that no cited item carries are violations."""
    in_reply = tuple(dict.fromkeys(amounts_cop(reply)))
    allowed = sorted({amount for citation in citations for amount in citation.amounts_cop})
    violations = tuple(amount for amount in in_reply if amount not in allowed)
    return PriceCheck(not violations, in_reply, tuple(allowed), violations)


def check_groundedness(
    output: AgentTurnOutput,
    citations: Sequence[Citation],
    *,
    names: Iterable[str] = (),
    fallback: str = "",
    customer_message: str | None = None,
    consent_prompt: str = "",
) -> GroundednessCheck:
    """R3: citations resolve to active items, and a reply without them claims nothing."""
    known = {citation.id for citation in citations}
    problems = [
        f"cites {fact_id}, which is not an active fact or product of this seller"
        for fact_id in output.cited_fact_ids
        if fact_id not in known
    ]
    if not output.cited_fact_ids and output.handoff_reason is None:
        declined = output.objection_tag == "off_topic" and not _has_number(output.reply)
        if not declined and makes_claim(
            output.reply,
            names=names,
            fallback=fallback,
            customer_message=customer_message,
            consent_prompt=consent_prompt,
        ):
            problems.append("states something about the business without citing any fact")
    return GroundednessCheck(not problems, tuple(problems))


def makes_claim(
    reply: str,
    *,
    names: Iterable[str] = (),
    fallback: str = "",
    customer_message: str | None = None,
    consent_prompt: str = "",
) -> bool:
    """Whether ``reply`` states something; greeting, thanking and asking do not (R3).

    A digit or an amount is always a claim. Outside its questions and requests a reply may
    hold only courtesy words and the shop's and owner's names; inside them, also question
    words and neutral nouns. A yes or no to a question the customer asked is a claim too.
    The fallback sentence and the seller's own consent question are not.
    """
    if consent_prompt:
        reply = reply.replace(consent_prompt, " ")
    if _has_number(reply):
        return True
    if fallback and _words(reply) == _words(fallback):
        return False
    allowed = COURTESY | {word for name in names for word in _words(name)}
    statement = _QUESTION.sub(" ", reply)
    asked = _QUESTION.findall(reply)
    for sentence in _SENTENCE.findall(statement):
        words = _words(sentence)
        if words and words[0] in REQUESTS:
            asked.append(sentence)
            statement = statement.replace(sentence, " ", 1)
    if not set(_words(statement)) <= allowed:
        return True
    if any(not set(_words(ask)) <= allowed | QUESTION_WORDS | REQUESTS for ask in asked):
        return True
    return bool(customer_message and _asks(customer_message) and set(_words(reply)) & ANSWER_WORDS)


def _asks(message: str) -> bool:
    words = _words(message)
    return "?" in message or bool(words and words[0] in ASKING)


def _has_number(text: str) -> bool:
    return any(char.isdigit() for char in text) or bool(amounts_cop(text))


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", plain)


def review(
    output: AgentTurnOutput,
    citations: Sequence[Citation],
    *,
    owner_name: str,
    business_name: str = "",
    customer_message: str | None = None,
    consent_prompt: str = "",
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    low_confidence_streak: int = 0,
    retried: bool = False,
) -> Review:
    """Decide on one answer. ``citations`` are what the catalog resolved for its cited ids."""
    fallback = FALLBACK_REPLY.format(owner_name=owner_name)
    price = check_prices(output.reply, citations)
    groundedness = check_groundedness(
        output,
        citations,
        names=(owner_name, business_name),
        fallback=fallback,
        customer_message=customer_message,
        consent_prompt=consent_prompt,
    )
    confidence = output.confidence if groundedness.passed else 0.0

    def decide(
        outcome: Outcome, reply: str | None, reason: HandoffReason | None, streak: int
    ) -> Review:
        return Review(outcome, reply, reason, streak, confidence, price, groundedness, retried)

    if not price.passed:
        if not retried:
            return decide("retry", None, None, low_confidence_streak)
        return decide("fallback", fallback, "price_unverified", low_confidence_streak)
    if output.handoff_reason is not None:
        return decide("accept", output.reply, output.handoff_reason, 0)
    if confidence < low_confidence_threshold:
        streak = low_confidence_streak + 1
        return decide(
            "fallback", fallback, "low_confidence_twice" if streak >= 2 else "other", streak
        )
    return decide("accept", output.reply, None, 0)


def retry_instruction(result: Review) -> str:
    """The note the guided retry adds to the context (R2): what was wrong, what to do."""
    amounts = ", ".join(format_cop(amount) for amount in result.price.violations)
    return (
        f"Tu respuesta anterior incluía montos que no aparecen en ningún dato citado: {amounts}. "
        "Responde otra vez usando solo precios y montos que estén en los datos que citas en "
        "cited_fact_ids. Si no tienes el dato, di que no lo tienes y pasa la conversación al "
        "dueño del negocio."
    )
