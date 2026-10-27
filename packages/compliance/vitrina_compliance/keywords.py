"""Keywords that act before any model sees a message (R7, R8; FR-063; AS-6.1, AS-6.5, AS-6.6).

Matching is case- and accent-insensitive and ignores punctuation, emoji and courtesy words,
so "BAJA", "baja por favor" and "Ya no más 🙏" all opt out. A keyword must be the whole
message: "quiero cancelar mi pedido" is a question for the agent, whose ``opt_out`` tool
covers free-form requests. Consent replies follow the R8 lexicon and fail on any negation.
"""

import re
import unicodedata

OPT_OUT = (("baja",), ("no", "mas"), ("stop",), ("cancelar",))
ERASURE = ("borrar", "mis", "datos")
COURTESY = frozenset({"por", "favor", "porfa", "gracias", "ya", "please", "pls"})
# ADR-0009 / agent-tools.json R8: sí, si, claro, dale, listo, ok, de una, por favor avísame.
AFFIRMATIVE = (("si",), ("claro",), ("dale",), ("listo",), ("ok",), ("de", "una"),
               ("por", "favor", "avisame"))  # fmt: skip
NEGATION = frozenset({"no", "nunca", "jamas"})


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", plain)


def _without_courtesy(text: str) -> tuple[str, ...]:
    return tuple(word for word in words(text) if word not in COURTESY)


def is_opt_out_request(text: str) -> bool:
    return _without_courtesy(text) in OPT_OUT


def is_erasure_request(text: str) -> bool:
    return _without_courtesy(text) == ERASURE


def is_affirmative(text: str) -> bool:
    tokens = tuple(words(text))
    if NEGATION.intersection(tokens):
        return False
    return any(tokens[: len(phrase)] == phrase for phrase in AFFIRMATIVE)
