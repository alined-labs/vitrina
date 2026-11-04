"""The deals a text promises: percentages, 2x1, instalments, half price, free shipping (R2).

Like an amount, a deal may only be stated when a cited item states it, so the same reader serves
the price validator and the ad copy check, and reads what they cite too. It is deliberately
specific, because each false alarm costs a retry and can end in a handoff:

- what a shop says about its goods is no deal: ``100 % algodón``, ``10 % de niacinamida``,
  ``para regalar``, ``2x3 metros``, ``un tapete de 3x2``. A percentage describes goods only by
  what comes right after it, and never in a clause that talks of a discount or gives something;
- a no takes back the deal it is tied to, not the rest of the clause: ``No manejamos cuotas``,
  ``El 2x1 no aplica``, while ``¿Por qué no aprovechas el 30 %?`` and ``30 % hoy no más`` promise;
- wondering is no claim (``Te aviso si hay promo``, ``Cuando estén en oferta te aviso``), and
  neither is really handing the question off (``Te paso con Ana para lo del 2x1``). Words near a
  handoff do not shelter a deal: ``Para confirmar tu pedido te hago 30 %`` promises;
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
    "cinco": 5, "diez": 10, "doce": 12, "quince": 15, "veinte": 20, "veinticinco": 25, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
}  # fmt: skip
# "2x1", "3 por 2", "dos por uno", never "2 por 85000", "2 por 15 mil" or a length "2x1,40".
_TAKE_PAY = re.compile(
    r"\b([2-9]|dos|tres|cuatro|cinco)\s*(?:x|×|por)\s*([1-9]|uno|dos|tres|cuatro)\b"
    r"(?!\s*(?:mil\b|\d)|[.,]\d)"
)
# Lengths are written the same way: "2 x 1 metros", "un tapete de 3x2", unlike "promo de 2x1".
_MEASURE_AFTER = re.compile(r"\s*(?:m|mts?|metros?|cm|mm|pulgadas?|varas?)\b")
_MEASURE_BEFORE = re.compile(r"(?<!promo )(?<!promocion )(?<!oferta )(?<!descuento )\bde\s*$")
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
_DISCOUNT_WORD = r"(?:descuent\w*|rebaj\w*|dcto|dto)"
_DEAL_WORD = r"(?:promo|promos|promocion|promociones|oferta|ofertas)"
# In a clause with one of these a percentage is a deal, whatever follows it.
_DEAL_TALK = re.compile(
    rf"\b(?:{_DISCOUNT_WORD}|{_DEAL_WORD}|ahorr\w*|off|menos|barat\w*|anticipo|abono|napa|regalo)\b"
)
# Giving something: "te hago", "te doy", "ella te deja". No handoff shelters a clause with these.
_GIVING_VERB = re.compile(
    r"\b(?:hago|hacemos|haga|hace|hacen|doy|damos|den|da|dan|te\s+de|dejo|dejamos|deje|deja|aplico"
    r"|aplicamos|regalo|regalamos|ofrezco|ofrecemos|tienes)\b"
)
# What comes right after a percentage that describes goods: "80 % algodón", "60 % de lana".
_MATERIAL = re.compile(
    r"\s*(?:algodon|cuero|lana|seda|lino|poliester|nylon|nailon|hilo|fique|licra|lycra|spandex"
    r"|elastano|acrilico|viscosa|natural|organic\w*|reciclad\w*|cacao|fruta|pur[oa])\b"
)
# "10 % de niacinamida", "99 % de los rayos": of what it is made or what it does.
_OF_SOMETHING = re.compile(r"\s*de\s+(?:(?:la|las|el|los)\s+)?[a-z]")
# "20 % más liviana", against "20 % más barata", which _DEAL_TALK catches.
_COMPARED = re.compile(r"\s*mas\s+[a-z]")
_TAX_BEFORE = re.compile(r"\biva\s+(?:del?\s+)?$")
_INSTALLMENTS = re.compile(
    r"\b(?:\d+|dos|tres|cuatro|cinco|seis|doce)\s+cuot(?:as|icas|itas)\b"
    r"|\b(?:en|a|por|hasta)\s+(?:\w+\s+)?cuot(?:as|icas|itas)\b"
    r"|\bcuotas\s+(?:sin|de|mensuales)\b"
    r"|\b(?:aceptamos|recibimos|manejamos|tenemos|ofrecemos|hacemos)\s+(?:pagos?\s+(?:en|a)\s+)?cuotas\b"
    r"|\b(?:en|a)\s+(?:\d+|dos|tres|cuatro|cinco|seis|doce)\s+pagos\b"
    r"|\bdivid\w+\s+(?:\w+\s+)?en\s+\w+\s+pagos\b"
    r"|\bdifer\w+\s+a\s+\d+\s+meses\b"
    r"|\ba\s+(?:credito|plazos)\b"
    r"|\bfinanci(?:amos|ado|ada|acion|amiento)\b"
    r"|\bsin\s+intere(?:s|ses)\b"
    r"|\bcon\s+(?:addi|sistecredito)\b"
    r"|\bte\s+(?:la|lo)\s+fio\b"
)
_HALF_PRICE = re.compile(
    r"\bmitad\s+del?\s+precio\b|\bmedio\s+precio\b"
    r"|\b(?:vale|valen|cuesta|cuestan|sale|salen|queda|quedan|dejo|dejamos|doy|damos|vendo|vendemos)"
    r"\s+(?:\w+\s+)?(?:a|por|en)\s+la\s+mitad\b"
    r"|\bsegund[oa]\s+(?:\w+\s+)?(?:a|por)\s+la\s+mitad\b"
)
_DISCOUNT = re.compile(
    r"\b(?:hago|hacemos|doy|damos|tengo|tenemos|hay|aplico|aplicamos|ofrecemos|manejamos|tiene|tienen"
    r"|(?<!algo )con|tienes\s+(?:un|una))\s+"
    r"(?:(?:un|una|el|la|ese|este|algo|de|buen|buena|gran|especial|pequeno)\s+){0,3}"
    rf"{_DISCOUNT_WORD}\b"
    r"|\b(?:te|se|le)\s+(?:(?:la|lo|las|los)\s+)?(?:rebajo|rebajamos|descuento|descontamos)\b"
    r"|\bte\s+bajo\s+\d|\bte\s+(?:bajo|mejoro)\s+el\s+precio\b"
    rf"|\ben\s+(?:{_DISCOUNT_WORD}|{_DEAL_WORD})\b"
    rf"|\b(?:esta|estan|va|van)\s+de\s+{_DEAL_WORD}\b"
    r"|\b(?:esta|estan)\s+rebajad[oa]s?\b|\bprecio\s+rebajado\b"
    rf"|\b(?:tenemos|hay|tengo)\s+(?:(?:un|una|esta|gran|buena)\s+){{0,2}}{_DEAL_WORD}\b"
    r"(?!\s+de\s+(?:colores|trabajo))"
    rf"|\baprovech\w*\s+(?:(?:la|el|esta|este|nuestra|nuestro|tu)\s+)?(?:{_DEAL_WORD}|{_DISCOUNT_WORD})\b"
    r"|\bprecio\s+especial\b"
    r"|\b(?:hago|dejo|doy|damos|dejamos)\s+(?:un\s+)?(?:buen|mejor)\s+precio\b"
    r"|\b(?:dejo|dejamos|doy|damos|vendo|vendemos|sale|salen|queda|quedan)\s+(?:\w+\s+)?mas\s+barat[oa]s?\b"
    r"|\bahorras?\s+\$?\d"
    rf"|\b{_DISCOUNT_WORD}\s*:?\s*(?:es\s+)?(?:del?\s+)?\$?\d"
    r"|\b(?:estamos\s+de\s+remate|en\s+liquidacion)\b"
)
_FREE = re.compile(
    r"\bgratis\b|\bgratuit[oa]s?\b|\bsin\s+(?:ningun\s+)?(?:costo|cargo|recargo)\b(?!\s+de\b)"
    r"|\bno\s+(?:te\s+|le\s+)?cobr\w+\s+(?:el\s+)?(?:envio|domicilio)\b"
    r"|\b(?:envios?|domicilios?)\s+(?:va\s+|van\s+)?incluidos?\b"
    r"|\bincluye\s+(?:el\s+)?(?:envio|domicilio)\b"
    r"|\bno\s+(?:te\s+|le\s+)?(?:tiene|tienen)\s+(?:ningun\s+)?(?:costo|cargo)\b"
    r"|\bno\s+(?:te\s+|le\s+)?cuesta\s+nada\b"
    r"|\bno\s+(?:paga|pagas|pagan)\s+(?:el\s+)?(?:envio|domicilio)\b"
    r"|\bte\s+regal\w+\s+(?:el\s+)?(?:envio|domicilio)\b"
    r"|\bte\s+regalamos\b(?!\s+(?:el|la|tu|su)\s+(?:dato|numero|contacto|informacion|nombre))"
    r"|\bpor\s+(?:nuestra\s+cuenta|cuenta\s+nuestra)\b"
    r"|\b(?:envios?|domicilios?)\s+(?:va|van)\s+de\s+cortesia\b|\bde\s+napa\b"
)
_SHIPPING = re.compile(r"\b(?:envios?|domicilios?|despachos?|transporte|flete)\b")
# In a cited text a plain mention states the deal: "Cuotas con tarjeta", "Promo 2x1".
_STATED_DISCOUNT = re.compile(rf"\b(?:{_DISCOUNT_WORD}|{_DEAL_WORD})\b")
_STATED_INSTALLMENTS = re.compile(r"\bcuotas?\b")

# Clauses: a deal stands or falls with the no or the handoff next to it, not with one elsewhere.
_CLAUSE_BREAK = re.compile(
    r"[!?¿¡;\n]|:(?!\s*[\d$])|[.,](?!\d)|\b(?:pero|aunque)\b"
    r"|\by\b(?!\s+(?:paga|pagas|pague|lleva|llevas|lleve)\b)"
)
# A "no" that takes nothing back: "No dudes en escribirnos", "Ni lo dudes". Folded.
COURTESY_NO = re.compile(
    r"\b(?:no|nunca) (?:hay problema|hay lio|te preocupes|se preocupe|dudes|dude|duden"
    r"|olvides|olvide|olviden|te pierdas|se pierda|te quedes sin|te vas a arrepentir"
    r"|te arrepentiras|se va a arrepentir)\b|\bni lo dudes\b|\bpor que no\b"
)
_REFUSING = (
    r"(?:hay|habra|tenemos|tengo|tiene|tienen|manejamos|manejo|maneja|manejan|hacemos|hago|ofrecemos"
    r"|ofrezco|aceptamos|aplica|aplican|aplicamos|aplico|es|son|esta|estan|damos|doy|puedo|podemos"
    r"|vendemos|incluye|incluyen|contamos|trabajamos|va|van|sale|salen|queda|quedan)"
)
_PRONOUNS = r"(?:(?:te|le|les|lo|la|los|las|se|nos)\s+)*"
# "No hay ese 2x1", "no te puedo prometer envío gratis": a refusing verb governs what follows it.
_REFUSED_BEFORE = re.compile(
    rf"\b(?:no|nunca|tampoco)\s+{_PRONOUNS}{_REFUSING}\b((?:\s+\w+){{0,4}})\s*$"
)
# The deal's own verb is said no to: "No hacemos descuentos", "no te hago descuento".
_NO_RIGHT_BEFORE = re.compile(rf"\b(?:no|nunca|tampoco)\s+{_PRONOUNS}$")
_NEAR_NO = re.compile(
    r"\b(?:ni|nada\s+de|ningun|ninguna|ninguno)\s+(?:\w+\s+)?$"
    r"|\bsin\s+(?:(?:el|la|los|las|ningun|ninguna|algun|alguna)\s+)?$"
)
# "El 2x1 no aplica", "esa promo ya no está", "en cuotas imposible", but not "hoy no más".
_REFUSED_AFTER = re.compile(
    rf"\s*(?:\w+\s+){{0,2}}?(?:(?:ya\s+)?no\s+{_PRONOUNS}{_REFUSING}\b|imposible\b|no\s+se\s+puede\b)"
)
# Only wondering: "si hay", "cuando haya", "cuando estén en promo". "Si compras 2 te hago 30 %"
# still promises.
_WONDERS = re.compile(
    r"\b(?:si|cuando)\s+(?:(?:se\s+)?(?:hay|haya|hubiera|tiene|tienen|tenemos|manejan|manejamos"
    r"|aplica|aplican|puede|pueden|podemos|esta|estan|este|esten|salga|salgan|llegue|lleguen))\b"
)
_BUSCAS = re.compile(r"\bbusca[sn]?\b")
# Really handing off: the question goes to someone else, and nothing is given in the same breath.
_HANDS_OFF = re.compile(
    r"\b(?:te\s+comunico\s+con|te\s+paso\s+con|le\s+paso|le\s+pregunto|le\s+preguntamos|le\s+consulto"
    r"|te\s+pongo\s+en\s+contacto|voy\s+a\s+preguntar\w*|comunicarte\s+con)\b"
)
# Naming a word deal to talk about it: "lo del envío gratis", unless it is still on.
_ABOUT = re.compile(r"\b(?:lo\s+del?|solicitud\s+del?|peticion\s+del?)\s+(?:\w+\s+)?$")
_STILL_ON = re.compile(r"\b(?:sigue|siguen|vigente|vigentes)\b")


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


def _describes_goods(clause: str, match: re.Match[str]) -> bool:
    """Whether a percentage is about the goods or a tax, not a deal."""
    if _DEAL_TALK.search(clause) or _GIVING_VERB.search(clause):
        return False
    after = clause[match.end() :]
    return bool(
        _MATERIAL.match(after)
        or _OF_SOMETHING.match(after)
        or _COMPARED.match(after)
        or _TAX_BEFORE.search(clause[: match.start()])
    )


def _percentages(clause: str) -> list[tuple[re.Match[str], str]]:
    found = []
    for match in _PERCENT.finditer(clause):
        if not _describes_goods(clause, match):
            digits, words = match.groups()
            found.append((match, f"{int(digits) if digits else _PERCENT_WORDS[words]} %"))
    return found


def _take_pay(clause: str) -> list[tuple[re.Match[str], str]]:
    found = []
    for pattern, swapped in ((_TAKE_PAY, False), (_BUY_GET, False), (_PAY_GET, True)):
        for match in pattern.finditer(clause):
            take, pay = (_count(group) for group in match.groups())
            take, pay = (pay, take) if swapped else (take, pay)
            measure = _MEASURE_AFTER.match(clause, match.end()) or _MEASURE_BEFORE.search(
                clause[: match.start()]
            )
            if take > pay and not measure:
                found.append((match, f"{take}x{pay}"))
    return found


def promotions(text: str) -> set[str]:
    """The percentages and "2x1" forms in ``text``, stated or not: a customer naming one counts."""
    return {
        label
        for clause in _clauses(_fold(text))
        for _, label in (*_percentages(clause), *_take_pay(clause))
    }


def _denied(clause: str, match: re.Match[str]) -> bool:
    """Whether a no is tied to the deal ``match`` found, before or right after it."""
    before = COURTESY_NO.sub(" ", clause[: match.start()]).rstrip() + " "
    after = COURTESY_NO.sub(" ", clause[match.end() :])
    refused = _REFUSED_BEFORE.search(before)
    if refused and not _GIVING_VERB.search(refused.group(1)):
        return True
    return bool(
        _NO_RIGHT_BEFORE.search(before) or _NEAR_NO.search(before) or _REFUSED_AFTER.match(after)
    )


def _claims_in(clause: str) -> list[tuple[re.Match[str], str, bool]]:
    """The deals in one clause: (match, label, whether a number states it)."""
    found = [(match, label, True) for match, label in (*_percentages(clause), *_take_pay(clause))]
    found += [(match, INSTALLMENTS, False) for match in _INSTALLMENTS.finditer(clause)]
    found += [(match, HALF_PRICE, False) for match in _HALF_PRICE.finditer(clause)]
    found += [(match, DISCOUNT, False) for match in _DISCOUNT.finditer(clause)]
    for match in _FREE.finditer(clause):
        found.append((match, FREE_SHIPPING if _SHIPPING.search(clause) else FREE, False))
    return found


def _stands(clause: str, match: re.Match[str], numeric: bool) -> bool:
    """Whether the deal ``match`` found is stated: not refused, wondered about or handed off."""
    if _denied(clause, match) or _WONDERS.search(clause[: match.end()]):
        return False
    if _HANDS_OFF.search(clause) and not _GIVING_VERB.search(clause):
        return False
    if _BUSCAS.search(clause[: match.start()]) and not numeric:
        return False
    about = _ABOUT.search(clause[: match.start()]) and not _STILL_ON.search(clause)
    return numeric or not about


def offer_claims(text: str) -> set[str]:
    """The deals ``text`` promises, one label each: ``30 %``, ``2x1``, ``cuotas``, ``gratis``…"""
    return {
        label
        for clause in _clauses(_fold(text))
        for match, label, numeric in _claims_in(clause)
        if _stands(clause, match, numeric)
    }


def stated_offers(source: str) -> set[str]:
    """What a cited text states: its claims, and the deals it mentions without saying no. A colon
    joins what it labels: "Cuotas: no manejamos." says no."""
    joined = source.replace(":", " ")
    stated = offer_claims(joined)
    for clause in _clauses(_fold(joined)):
        for pattern, label in (
            (_STATED_DISCOUNT, DISCOUNT),
            (_STATED_INSTALLMENTS, INSTALLMENTS),
        ):
            match = pattern.search(clause)
            if match and not _denied(clause, match):
                stated.add(label)
    return stated


def unsupported_offers(text: str, sources: Iterable[str]) -> tuple[str, ...]:
    """The deals ``text`` promises that no text in ``sources`` states, sorted. A source's deal of
    any kind supports a general "descuento", and free shipping a general "gratis"."""
    stated: set[str] = set().union(*(stated_offers(source) for source in sources))
    if stated - {INSTALLMENTS, FREE, FREE_SHIPPING}:
        stated.add(DISCOUNT)
    if FREE_SHIPPING in stated:
        stated.add(FREE)
    return tuple(sorted(offer_claims(text) - stated))
