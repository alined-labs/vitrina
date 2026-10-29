"""The checks that run outside the model on every answer (R2–R5; FR-026; ADR-0006).

``review`` judges one ``AgentTurnOutput`` against the active items it cites and decides what
the customer gets:

- R2 price: every amount in the reply (``vitrina_core.money``, the normalizer that filled the
  catalog's ``amounts_cop``) must be an amount of a cited item. A yes to an amount or a
  promotion the customer named ("¿Me deja la mochila en 60 mil?" → "Te la dejo en ese
  precio") states it too, unless a cited item carries it. A violation earns one guided retry;
  a second one gets the fallback and a ``price_unverified`` handoff (AS-3.4).
- R3 groundedness: every cited id must be an active item of this seller; one unknown id is
  enough to count the answer as confidence 0. A reply without citations may only greet,
  thank, ask the customer something, decline an off-topic request or say the fallback
  sentence; anything else is an uncited claim and counts as confidence 0. Handoff replies
  need no citation (agent-tools.json ``cited_fact_ids``).
- R4 confidence: below the seller's threshold (default 0.6) the fallback replaces the reply
  and the turn counts as low confidence; the second in a row hands off.
- R5: a handoff the model chose keeps its reason and its reply (still under R2).
"""

import re
import unicodedata
from collections.abc import Collection, Iterable, Sequence
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
# A reply agrees to the customer's own price with one of these, "sí" (with its accent: "si" is
# "if") or an opening "si,"; it does not when it also says no. Folded, like the sets above.
AGREEING = frozenset({
    "claro", "correcto", "dale", "exacto", "listo", "obvio", "ok", "okay", "perfecto", "supuesto",
})  # fmt: skip
NEGATIONS = frozenset({"no", "ningun", "ninguna", "ninguno", "nunca", "tampoco"})
_YES = re.compile(r"(?<!\w)sí(?!\w)|^\W*si\s*[,.!]")
# Closings whose "no" takes nothing back: "No dudes en escribirnos", "Nunca te vas a arrepentir".
_NOT_A_NO = re.compile(
    r"\b(?:no|nunca) (?:hay problema|hay lio|te preocupes|se preocupe|dudes|dude|duden"
    r"|olvides|olvide|olviden|te pierdas|se pierda|te vas a arrepentir|te arrepentiras"
    r"|se va a arrepentir)\b"
)
# A price verb pointing back at the customer's amount: "te la dejo en ese precio", "sigue en
# ese precio", "queda en eso". Folded text.
_CONCEDES = re.compile(
    r"\b(?:dejo|dejamos|doy|damos|vendo|vendemos|hago|hacemos|queda|quedan|sigue|siguen|sale"
    r"|salen|esta|estan)\s+(?:en|a)\s+(?:ese|esa|eso|esos|esas)\b"
)
# Promotions the money reader has no amount for: "2x1", "3 por 2", "dos por uno", "10 %".
_PROMOTION = re.compile(
    r"\b([2-9]|dos|tres|cuatro|cinco)\s*(?:x|×|por)\s*([1-9]|uno|dos|tres|cuatro)\b"
    r"(?!\s*(?:mil|\d))|\b(\d{1,2})\s*(?:%|por\s*ciento)"
)
_DIGITS = {"uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5"}


@dataclass(frozen=True)
class PriceCheck:
    """``violations``: the amounts no cited item carries, written or agreed to; ``agreed``: what
    the reply said yes to, as the customer named it ("$60.000", "2x1")."""

    passed: bool
    amounts_in_reply: tuple[int, ...]
    amounts_in_citations: tuple[int, ...]
    violations: tuple[int, ...]
    agreed: tuple[str, ...] = ()


@dataclass(frozen=True)
class Agreement:
    """The customer's amounts and promotions a reply says yes to."""

    amounts: tuple[int, ...] = ()
    promotions: tuple[str, ...] = ()

    def labels(self) -> tuple[str, ...]:
        return (*(format_cop(amount) for amount in self.amounts), *self.promotions)


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


def check_prices(
    reply: str,
    citations: Sequence[Citation],
    *,
    customer_message: str | None = None,
    cited_texts: Iterable[str] = (),
    consent_prompt: str = "",
) -> PriceCheck:
    """R2: the amounts in ``reply`` that no cited item carries are violations, and so are the
    customer's own amounts and promotions it says yes to (``cited_texts`` may state those)."""
    in_reply = tuple(dict.fromkeys(amounts_cop(reply)))
    allowed = sorted({amount for citation in citations for amount in citation.amounts_cop})
    stated = tuple(amount for amount in in_reply if amount not in allowed)
    agreement = agreed_offers(
        reply.replace(consent_prompt, " ") if consent_prompt else reply,
        customer_message,
        allowed,
        cited_texts,
    )
    violations = (*stated, *(amount for amount in agreement.amounts if amount not in stated))
    agreed = agreement.labels()
    return PriceCheck(not (violations or agreed), in_reply, tuple(allowed), violations, agreed)


def agreed_offers(
    reply: str,
    customer_message: str | None,
    allowed: Collection[int],
    cited_texts: Iterable[str] = (),
) -> Agreement:
    """The amounts and promotions in ``customer_message`` that no cited item carries and that
    ``reply`` says yes to, read sentence by sentence. A sentence without a no of its own that
    points back at them ("te la dejo en ese precio") or repeats the promotion agrees, whatever
    the other sentences say; a bare yes agrees when the reply quotes no amount and no sentence
    says no ("No, veci, ese precio ya no está. ¡Claro que te ayudo!" agrees to nothing)."""
    if not customer_message:
        return Agreement()
    amounts = tuple(
        amount for amount in dict.fromkeys(amounts_cop(customer_message)) if amount not in allowed
    )
    supported = set().union(*(_promotions(text) for text in cited_texts))
    promotions = tuple(sorted(_promotions(customer_message) - supported))
    if not (amounts or promotions):
        return Agreement()
    statement = _QUESTION.sub(" ", reply)
    refuses = says_yes = False
    for sentence in _SENTENCE.findall(statement):
        folded = _fold(sentence)
        if set(_words(_NOT_A_NO.sub(" ", folded))) & NEGATIONS:
            refuses = True
            continue
        if _CONCEDES.search(folded) or _promotions(sentence) & set(promotions):
            return Agreement(amounts, promotions)
        plain = unicodedata.normalize("NFC", sentence.casefold()).strip()
        says_yes = says_yes or bool(_YES.search(plain) or set(_words(folded)) & AGREEING)
    if says_yes and not refuses and not amounts_cop(statement):
        return Agreement(amounts, promotions)
    return Agreement()


def _promotions(text: str) -> set[str]:
    found: set[str] = set()
    for match in _PROMOTION.finditer(_fold(text)):
        take, pay, percent = match.groups()
        found.add(
            f"{int(percent)} %" if percent else f"{_DIGITS.get(take, take)}x{_DIGITS.get(pay, pay)}"
        )
    return found


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


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in folded if not unicodedata.combining(char))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _fold(text))


def review(
    output: AgentTurnOutput,
    citations: Sequence[Citation],
    *,
    owner_name: str,
    business_name: str = "",
    customer_message: str | None = None,
    consent_prompt: str = "",
    cited_texts: Iterable[str] = (),
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    low_confidence_streak: int = 0,
    retried: bool = False,
) -> Review:
    """Decide on one answer. ``citations`` are what the catalog resolved for its cited ids, and
    ``cited_texts`` what the agent read about them."""
    fallback = FALLBACK_REPLY.format(owner_name=owner_name)
    price = check_prices(
        output.reply,
        citations,
        customer_message=customer_message,
        cited_texts=cited_texts,
        consent_prompt=consent_prompt,
    )
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
        return decide("fallback", fallback, "low_confidence_twice" if streak >= 2 else None, streak)
    return decide("accept", output.reply, None, 0)


def retry_instruction(result: Review) -> str:
    """The note the guided retry adds to the context (R2): what was wrong, what to do."""
    price = result.price
    stated = ", ".join(
        format_cop(amount) for amount in price.violations if amount in price.amounts_in_reply
    )
    wrong = []
    if stated:
        wrong.append(
            f"Tu respuesta anterior incluía montos que no aparecen en ningún dato citado: {stated}. "
        )
    if price.agreed:
        wrong.append(
            "Tu respuesta anterior le decía que sí a un precio o una promoción del cliente que "
            f"no está en ningún dato citado: {', '.join(price.agreed)}. "
        )
    return "".join(wrong) + (
        "Responde otra vez usando solo precios y montos que estén en los datos que citas en "
        "cited_fact_ids. Si no tienes el dato, di que no lo tienes y pasa la conversación al "
        "dueño del negocio."
    )
