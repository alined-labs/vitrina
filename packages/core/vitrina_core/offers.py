"""The deals a text promises: percentages, 2x1, instalments, half price, free shipping (R2).

Like an amount, a deal may only be stated when a cited item states it, so the same reader serves
the price validator and the ad copy check, and reads what they cite too. It is deliberately
specific, because each false alarm costs a retry and can end in a handoff:

- what a shop says about its goods is no deal: ``100 % algodón``, ``80 % lana``, ``para regalar``,
  ``2x3 metros``, ``por la mitad``;
- a clause that says no is no claim (``No manejamos cuotas``, ``El 2x1 no aplica``), and neither
  is one that passes the question on or only wonders (``Te comunico con Ana para revisar lo del
  descuento``, ``Te aviso si hay promo``);
- a question is no shelter: ``¿Te sirve con 30 % de descuento?`` offers it.
"""

import re
import unicodedata
from collections.abc import Iterable

DISCOUNT = "descuento"
INSTALLMENTS = "cuotas"
HALF_PRICE = "mitad de precio"
FREE_SHIPPING = "envío gratis"
FREE = "gratis"

_NUMBER_WORDS = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
_PERCENT_WORDS = {
    "cinco": 5, "diez": 10, "quince": 15, "veinte": 20, "veinticinco": 25, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
}  # fmt: skip
# "2x1", "3 por 2", "dos por uno", never "2 por 85000" or "2 por 15 mil".
_TAKE_PAY = re.compile(
    r"\b([2-9]|dos|tres|cuatro|cinco)\s*(?:x|×|por)\s*([1-9]|uno|dos|tres|cuatro)\b"
    r"(?!\s*(?:mil\b|\d))"
)
# Lengths are written the same way: "2 x 1 metros".
_MEASURE_AFTER = re.compile(r"\s*(?:m|mts?|metros?|cm|mm|pulgadas?|varas?)\b")
_BUY_GET = re.compile(
    r"\b(?:lleva|llevas|lleve|llevando)\s+(\d|dos|tres|cuatro|cinco)\s+(?:y\s+)?(?:paga|pagas|pague)"
    r"\s+(\d|uno|dos|tres|cuatro)\b"
)
_PAY_GET = re.compile(
    r"\b(?:compra|compras|compre|paga|pagas|pague)\s+(\d|uno|dos|tres|cuatro)\s+(?:y\s+)?"
    r"(?:lleva|llevas|lleve)\s+(\d|dos|tres|cuatro|cinco)\b"
)
_PERCENT = re.compile(
    r"(?<![\d.,])(\d{1,2})\s*(?:%|por\s*ciento)"
    rf"|\b({'|'.join(_PERCENT_WORDS)})\s+por\s*ciento"
)
# What a percentage describes when it is not a deal: "80 % algodón", "70 % de cacao".
_COMPOSITION = re.compile(
    r"algodon|cuero|lana|seda|lino|poliester|nylon|nailon|hilo|fique|licra|lycra|spandex|elastano"
    r"|acrilico|viscosa|natural|organic|reciclad|cacao|fruta|humedad|pur[oa]"
)
_INSTALLMENTS = re.compile(
    r"\b(?:\d+|dos|tres|cuatro|cinco|seis|doce)\s+cuotas\b"
    r"|\b(?:en|a|por|hasta)\s+(?:\w+\s+)?cuotas\b"
    r"|\bcuotas\s+(?:sin|de|mensuales)\b"
    r"|\ba\s+(?:credito|plazos)\b"
    r"|\bfinanci(?:amos|ado|ada|acion|amiento)\b"
    r"|\bsin\s+intere(?:s|ses)\b"
)
_HALF_PRICE = re.compile(
    r"\bmitad\s+del?\s+precio\b|\bmedio\s+precio\b"
    r"|\b(?:vale|valen|cuesta|cuestan|sale|salen|queda|quedan|dejo|dejamos|doy|damos|vendo|vendemos)"
    r"\s+(?:\w+\s+)?(?:a|por|en)\s+la\s+mitad\b"
)
_DISCOUNT_WORD = r"(?:descuent\w*|rebaj\w*|dcto|dto)"
_DEAL_WORD = r"(?:promo|promos|promocion|promociones|oferta|ofertas)"
_DISCOUNT = re.compile(
    r"\b(?:hago|hacemos|doy|damos|tengo|tenemos|hay|aplico|aplicamos|ofrecemos|manejamos|con"
    r"|tienes\s+(?:un|una))\s+(?:(?:un|una|el|la|ese|este|algo|de|buen|buena|gran|especial|pequeno)\s+){0,3}"
    rf"{_DISCOUNT_WORD}\b"
    r"|\b(?:te|se|le)\s+(?:(?:la|lo|las|los)\s+)?(?:rebajo|rebajamos)\b"
    r"|\bte\s+(?:bajo|mejoro)\s+el\s+precio\b"
    rf"|\ben\s+(?:{_DISCOUNT_WORD}|{_DEAL_WORD})\b"
    rf"|\b(?:tenemos|hay|tengo)\s+(?:(?:un|una|esta|gran|buena)\s+){{0,2}}{_DEAL_WORD}\b(?!\s+de\s+(?:colores|trabajo))"
    r"|\bprecio\s+especial\b"
)
_FREE = re.compile(
    r"\bgratis\b|\bgratuit[oa]s?\b|\bsin\s+(?:ningun\s+)?(?:costo|cargo)\b"
    r"|\bno\s+(?:te\s+|le\s+)?cobr\w+\s+(?:el\s+)?(?:envio|domicilio)\b"
    r"|\b(?:envio|domicilio)\s+(?:va\s+)?incluido\b"
)
_SHIPPING = re.compile(r"\b(?:envios?|domicilios?|despachos?|transporte|flete)\b")
# In a cited text a plain mention states the deal: "Cuotas con tarjeta", "Promo 2x1".
_STATED_DISCOUNT = re.compile(rf"\b(?:{_DISCOUNT_WORD}|{_DEAL_WORD})\b")
_STATED_INSTALLMENTS = re.compile(r"\bcuotas?\b")

# Clauses: a deal stands or falls with the no or the handoff next to it, not with one elsewhere.
_CLAUSE_BREAK = re.compile(
    r"[!?¿¡;:\n]|[.,](?!\d)|\b(?:pero|aunque)\b|\by\b(?!\s+(?:paga|pagas|pague|lleva|llevas|lleve)\b)"
)
# A "no" that takes nothing back: "No dudes en escribirnos", "No te vas a arrepentir". Folded.
COURTESY_NO = re.compile(
    r"\b(?:no|nunca) (?:hay problema|hay lio|te preocupes|se preocupe|dudes|dude|duden"
    r"|olvides|olvide|olviden|te pierdas|se pierda|te vas a arrepentir|te arrepentiras"
    r"|se va a arrepentir)\b"
)
_NO_BEFORE = re.compile(
    r"\b(?:no(?! solo\b)|ni|ningun|ninguna|ninguno|nunca|tampoco)\b|\bsin(?: \w+){0,2} ?$"
)
_NO_AFTER = re.compile(r"\bno\b")
# Only wondering: "si hay", "cuando haya". A plain "si compras 2 te hago 30 %" still promises.
_WONDERS = re.compile(
    r"\b(?:si|cuando)\s+(?:(?:se\s+)?(?:hay|haya|hubiera|tiene|tienen|tenemos|manejan|manejamos"
    r"|aplica|aplican|puede|pueden|podemos))\b"
)
_PASSES_ON = re.compile(
    r"\b(?:te comunico con|te paso con|le paso|te pongo en contacto|comunicarte|consultar|consulto"
    r"|revisar|reviso|preguntar|preguntarle|pregunto|preguntas|verificar|confirmar|confirme"
    r"|te confirma|le confirma|validar|lo del?|solicitud|peticion|te refieres|hablas|viste)\b"
)


def _fold(text: str) -> str:
    """Lower case without accents, keeping "sí" apart from "si" ("Sí hay" against "si hay")."""
    marked = re.sub(r"\bsí\b", "sii", unicodedata.normalize("NFC", text.casefold()))
    return "".join(
        char for char in unicodedata.normalize("NFKD", marked) if not unicodedata.combining(char)
    )


def _clauses(folded: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE_BREAK.split(folded) if clause.strip()]


def _count(word: str) -> int:
    return _NUMBER_WORDS[word] if word in _NUMBER_WORDS else int(word)


def _percentages(folded: str) -> list[tuple[re.Match[str], str]]:
    found = []
    for match in _PERCENT.finditer(folded):
        digits, words = match.groups()
        after = folded[match.end() : match.end() + 24]
        before = folded[max(0, match.start() - 16) : match.start()]
        if _COMPOSITION.search(after) or _COMPOSITION.search(before):
            continue
        found.append((match, f"{int(digits) if digits else _PERCENT_WORDS[words]} %"))
    return found


def _take_pay(folded: str) -> list[tuple[re.Match[str], str]]:
    found = []
    for pattern, swapped in ((_TAKE_PAY, False), (_BUY_GET, False), (_PAY_GET, True)):
        for match in pattern.finditer(folded):
            take, pay = (_count(group) for group in match.groups())
            take, pay = (pay, take) if swapped else (take, pay)
            if take > pay and not _MEASURE_AFTER.match(folded, match.end()):
                found.append((match, f"{take}x{pay}"))
    return found


def promotions(text: str) -> set[str]:
    """The percentages and "2x1" forms in ``text``, stated or not: a customer naming one counts."""
    folded = _fold(text)
    return {label for _, label in (*_percentages(folded), *_take_pay(folded))}


def _found_in(clause: str) -> list[tuple[re.Match[str], str]]:
    found = [*_percentages(clause), *_take_pay(clause)]
    found += [(match, INSTALLMENTS) for match in _INSTALLMENTS.finditer(clause)]
    found += [(match, HALF_PRICE) for match in _HALF_PRICE.finditer(clause)]
    found += [(match, DISCOUNT) for match in _DISCOUNT.finditer(clause)]
    for match in _FREE.finditer(clause):
        found.append((match, FREE_SHIPPING if _SHIPPING.search(clause) else FREE))
    return found


def _stands(clause: str, match: re.Match[str]) -> bool:
    """Whether the deal ``match`` found is stated: no "no" beside it, not passed on, no "if"."""
    before = COURTESY_NO.sub(" ", clause[: match.start()])
    after = COURTESY_NO.sub(" ", clause[match.end() :])
    if _NO_BEFORE.search(before) or _NO_AFTER.search(after):
        return False
    return not (_PASSES_ON.search(clause) or _WONDERS.search(clause[: match.end()]))


def offer_claims(text: str) -> set[str]:
    """The deals ``text`` promises, one label each: ``30 %``, ``2x1``, ``cuotas``, ``gratis``…"""
    return {
        label
        for clause in _clauses(_fold(text))
        for match, label in _found_in(clause)
        if _stands(clause, match)
    }


def _stated_in(source: str) -> set[str]:
    """What a cited text states: its claims, and the deals it mentions without saying no."""
    stated = offer_claims(source)
    for clause in _clauses(_fold(source)):
        for pattern, label in (
            (_STATED_DISCOUNT, DISCOUNT),
            (_STATED_INSTALLMENTS, INSTALLMENTS),
        ):
            match = pattern.search(clause)
            if match and not _NO_BEFORE.search(clause[: match.start()]):
                stated.add(label)
    return stated


def unsupported_offers(text: str, sources: Iterable[str]) -> tuple[str, ...]:
    """The deals ``text`` promises that no text in ``sources`` states, sorted. A source's deal of
    any kind supports a general "descuento", and free shipping a general "gratis"."""
    stated: set[str] = set().union(*(_stated_in(source) for source in sources))
    if stated - {INSTALLMENTS, FREE, FREE_SHIPPING}:
        stated.add(DISCOUNT)
    if FREE_SHIPPING in stated:
        stated.add(FREE)
    return tuple(sorted(offer_claims(text) - stated))
