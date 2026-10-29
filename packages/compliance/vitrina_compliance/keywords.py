"""Keywords that act before any model sees a message (R7, R8; FR-063; AS-6.1, AS-6.5, AS-6.6).

Matching is case- and accent-insensitive and ignores punctuation, emoji and courtesy words,
so "BAJA", "baja por favor" and "Ya no más 🙏" all opt out. The whole message must be opt-out
phrases: "BAJA BAJA", "No me escriban más" and "Quiero darme de baja" opt out, and so does
"Baja, gracias Ana", whose closing thanks is a sign-off. "quiero cancelar mi pedido" is a
question for the agent, whose ``opt_out`` tool covers free-form requests. Consent is stricter
still (ADR-0009: tighten, never loosen): the whole reply must be R8 lexicon phrases, with only
"pues", "por favor" and "porfa" besides, so "si me mandas fotos" (si = if), "ok gracias" or
"me avisas cuando llegue la roja" is not consent and the agent asks again (C10, A-009).
"""

import re
import unicodedata

Phrases = tuple[tuple[str, ...], ...]

# "No me escriban más", "no me manden más mensajes": asking the shop to stop writing.
_STOP_SENDING = ("escriban", "escribas", "escriba", "manden", "mandes", "mande", "envien", "envies",
                 "envie")  # fmt: skip
OPT_OUT: Phrases = (
    ("baja",), ("no", "mas"), ("stop",), ("cancelar",), ("pare",), ("paren",),
    ("no", "mas", "mensajes"),
    *(("no", "me", verb, "mas") for verb in _STOP_SENDING),
    *(("no", "me", verb, "mas", "mensajes") for verb in _STOP_SENDING),
    ("darme", "de", "baja"), ("quiero", "darme", "de", "baja"), ("me", "quiero", "dar", "de", "baja"),
    ("dame", "de", "baja"), ("deme", "de", "baja"), ("denme", "de", "baja"),
)  # fmt: skip
ERASURE = ("borrar", "mis", "datos")
COURTESY = frozenset({"por", "favor", "porfa", "gracias", "ya", "please", "pls"})
# "Baja, gracias Ana", "Baja, gracias por todo": at most this many words after the last
# "gracias" are a sign-off, not part of the request.
CLOSING_WORDS = 2
# ADR-0009 / agent-tools.json R8: sí, si, claro, dale, listo, ok, de una, por favor avísame,
# and since A-009 the phrases that are themselves a request to be told.
AFFIRMATIVE: Phrases = (("si",), ("claro",), ("dale",), ("listo",), ("ok",), ("de", "una"),
                        ("por", "favor", "avisame"), ("avisame",), ("aviseme",), ("me", "avisas"),
                        ("claro", "que", "si"))  # fmt: skip
CONSENT_FILLERS: Phrases = (("pues",), ("por", "favor"), ("porfa",))
MAX_CONSENT_WORDS = 12  # a plain yes is short; anything longer is not consent (only tightens)


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", plain)


def _without_courtesy(tokens: list[str]) -> tuple[str, ...]:
    return tuple(word for word in tokens if word not in COURTESY)


def _made_of(tokens: tuple[str, ...], phrases: Phrases, fillers: Phrases = ()) -> bool:
    """``tokens`` are nothing but ``phrases`` (at least one) and ``fillers``."""
    # Walk phrase by phrase, without recursion: (position, a phrase seen so far).
    frontier, seen = [(0, False)], {(0, False)}
    while frontier:
        position, matched = frontier.pop()
        if position == len(tokens):
            if matched:
                return True
            continue
        for phrase in (*phrases, *fillers):
            end = position + len(phrase)
            if tokens[position:end] == phrase:
                state = (end, matched or phrase in phrases)
                if state not in seen:
                    seen.add(state)
                    frontier.append(state)
    return False


def is_opt_out_request(text: str) -> bool:
    tokens = words(text)
    readings = [tokens]
    if "gracias" in tokens:
        last = len(tokens) - 1 - tokens[::-1].index("gracias")
        if len(tokens) - last - 1 <= CLOSING_WORDS:
            readings.append(tokens[:last])
    return any(_made_of(_without_courtesy(reading), OPT_OUT) for reading in readings)


def is_erasure_request(text: str) -> bool:
    return _without_courtesy(words(text)) == ERASURE


def is_affirmative(text: str) -> bool:
    """The reply is nothing but lexicon phrases (at least one) and the fillers."""
    tokens = tuple(words(text))
    if not tokens or len(tokens) > MAX_CONSENT_WORDS:
        return False
    return _made_of(tokens, AFFIRMATIVE, CONSENT_FILLERS)
