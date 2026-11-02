"""Spanish money expressions to integer COP (FR-026, FR-085; constitution Art. III.2).

One normalizer for both sides of the price check: catalog extracts ``amounts_cop`` when a
fact or product is written, and the price validator extracts the amounts in a reply. If
the two parsed differently, honest replies would fail and invented prices could pass.

The reading fails closed: any number from 1.000 up is money unless it clearly is something
else, because a wrong "money" costs one guided retry and a wrong "not money" lets an invented
price reach a customer. ``Mochila: 45000`` and ``a 45000`` count with no word to say so.

Also counted, whatever their size: a number with ``$``, ``COP``, ``pesos`` or ``lucas``; a number
with a multiplier (``mil``, ``k``, ``millón``, or ``palos`` right after a number); spelled-out
amounts with ``mil``/``millón`` or a currency word; a plain number right after a price word
(``vale 85``), in words too (``cuesta noventa``); and an amount in another currency (``USD 20``,
``20 €``), which no cited peso amount matches. ``45 mil 900`` is 45.900, ``medio millón``
500.000, ``dos palos`` 2.000.000 and ``40-45 mil`` both ends.

Not money: percentages; phone numbers and links; dates and times written as such (``27/10``,
``2:30``, never ``38: 85``); years, unless they stand where a price goes (``a 2000``, ``Mochila:
2000``); sizes, units and counts (``1200 ml``, ``1000 mochilas``); part of an address (``local
1204``, ``# 45-1020``); and a number in a clause about a code, an order or a phone call (``Tu
código de reserva es 58213``), unless the clause also has a price word (``Tu pedido vale 45000``).

That reading is for what the shop says. What a customer wrote is read by ``marked_amounts``: only
what is marked or cued as money, because "Mi pedido es el 58213" offers no price.
"""

import re
import unicodedata

# A comma or full stop not inside a number is kept: "45 mil, 50 mil" is two amounts.
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-zñ]+|[$€#%/:\-–—]|[.,!?;](?!\d)")
# 1'250.000: an apostrophe marks the millions. 45 000: a space groups thousands, as some
# keyboards and writers do; the number is then read as written, so it needs a money mark.
_APOSTROPHE_MILLIONS = re.compile(r"(?<=\d)['’´`](?=\d{3}(?:[.,]\d{3})*(?!\d))")
_SPACE_GROUPED = re.compile(r"(?<![\d.,])\d{1,3}(?: \d{3})+(?![\d.,]| \d)")
# A link holds no price: "wa.me/573000000101", "https://tienda.co/p/45000".
_LINK = re.compile(r"\bhttps?://\S+|\bwww\.\S+|\b[a-z0-9\-]+\.(?:me|co|com|net|org|ly)/\S*")
# Colombian phone numbers, however they are grouped: mobile, landline, toll-free.
_PHONE = re.compile(
    r"(?<![\d.,$])(?:\+?57[\s.\-]?)?(?:(?:3\d{2}|60\d)[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}"
    r"|\(\d{1,3}\)\s*\d{3}[\s.\-]?\d{4}|01[\s.\-]?8000[\s.\-]?\d{6})(?![\d])"
)
# "Pedido No. 58213", "N° 58213", "Of. 1204": the mark says a number follows, not a sentence end.
_NUMBER_MARK = re.compile(
    r"\b(no|n|nro|num|of|tel|cel|ref|cod|apto|int|cra|cl|cr|kr|av|dg|tv)\s*[.°º]\s*(?=\d)"
)
# A code glued to a short word: "ABC1234", "XR2000". A currency or a price word is not: "COP85000".
_CODE = re.compile(
    r"(?<![a-z0-9])(?!cop|usd|eur|us\d|vale|son|por|sale|solo|en\d)[a-z]{2,4}\d{3,}[a-z0-9]*"
)
# A time or a date is written without spaces: "2:30", "27/10", while "38: 85 mil" is a label.
_TIME_OR_DATE = re.compile(
    r"(?<![\d.,:/])(?:(?:[01]?\d|2[0-4]):[0-5]\d(?::[0-5]\d)?|[0-3]?\d/[01]?\d(?:/\d{2,4})?"
    r"|[01]?\d/(?:19|20)\d{2})(?![\d:/])"
)

# A digit written as an emoji: "4️⃣" is a 4 and these two marks.
_KEYCAP_MARKS = {0xFE0F: None, 0x20E3: None}

_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
}  # fmt: skip
_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300,
    "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500,
    "quinientas": 500, "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800, "novecientos": 900,
    "novecientas": 900,
}  # fmt: skip
_THOUSAND = {"mil"}
_MILLION = {"millon", "millones", "mill"}
_THOUSANDS_SLANG = {"luca", "lucas", "luka", "lukas", "luquitas"}
# Millions, only right after a number: "dos palos", while "palo de escoba" is a stick.
_MILLIONS_SLANG = {"palo", "palos"}
# "cuesta un poco más": an article after a price word, not an amount.
_ARTICLES = {"un", "una", "uno"}
_CURRENCY_AFTER = {"pesos", "peso", "pesitos", "pesito", "cop"}
# Another currency: reported as written, so it can only fail the check.
_FOREIGN_AFTER = {"usd", "dolar", "dolares", "dolarcitos", "eur", "euro", "euros"}
_MARK_BEFORE = {"$", "€", "cop", "usd", "eur"}
# Price words before a plain number. Strong: any size counts, so a short "vale 85" is reported
# and fails the check instead of hiding a price. Via "en": "te queda en 85", while "te queda 85"
# and "está en 85" are weak: from 1.000 up, where a year-like number counts too. A strong word a
# few words before is distant: "el precio de la camisa es 85000".
_STRONG_CUES = {
    "precio", "precios", "vale", "valen", "valor", "cuesta", "cuestan", "costo", "costos",
    "total", "envio", "domicilio", "cobro", "cobramos", "tarifa",
}  # fmt: skip
_EN_CUES = {
    "queda", "quedan", "quedaria", "quedarian", "sale", "salen", "saldria", "saldrian", "deja",
    "dejan", "dejo", "dejamos", "vendo", "vendemos", "vende", "venden", "doy", "damos", "ofrezco",
}  # fmt: skip
_EN_LINKS = {"en", "a", "por"}
_EN_WEAK_CUES = {"esta", "estan", "estaria"}
# How far back a strong price word still makes a plain number from 1.000 up a price.
_CUE_REACH = 6
_SENTENCE_END = {".", "!", "?", ";"}
_WEAK_CUES = {"son", "seria", "serian", "sera", "seran", "pagar", "pago", "pagas", "abono",
              "descuento"}  # fmt: skip
_CUE_LINKS = {
    "en", "de", "es", "a", "por", ":", "-", "–", "—", "unos", "unas", "solo", "solamente",
    "apenas", "como", "aprox", "aproximadamente",
}  # fmt: skip
_CONNECTORS = {"y", "a", "o", "-", "–", "—"}
# A number followed by one of these counts something else.
_NON_MONEY_UNITS = {
    "dia", "dias", "hora", "horas", "semana", "semanas", "mes", "meses", "minuto", "minutos",
    "ano", "anos", "unidad", "unidades", "talla", "tallas", "cm", "metro", "metros", "kg",
    "kilo", "kilos", "gramos", "gr", "litro", "litros", "ml", "persona", "personas", "pieza",
    "piezas", "par", "pares", "cuota", "cuotas", "x", "mah", "mg", "g", "gb", "mb", "w", "watts",
    "vatios", "v", "km", "m", "mm", "cc", "oz", "onzas", "p", "dpi", "rpm", "kcal", "calorias",
    "btu", "h", "hr", "hrs", "min", "mins", "cuadra", "cuadras",
}  # fmt: skip
# One of something: "1 unidad". After a bigger number it prices it: "A 45000 unidad".
_SINGULAR_UNITS = {"unidad", "pieza", "par", "persona", "kilo", "metro", "litro"}
# One letter may be a unit or a typo for a thousand: a strong price word settles it ("Te queda en
# 70m"), and without one it is a unit ("Pesa 1200 g").
_LETTER_UNITS = {"g", "m", "p", "v", "w"}
# Where the stock of a plain number is kept: "Tengo 1200 en bodega".
_STOCK_PLACES = {"bodega", "stock", "inventario", "existencia", "existencias"}
# After a plain number with no price word, a plural noun says what it counts ("1000 mochilas",
# "3500 seguidores"); these plural words say nothing of the kind ("Mochila 45000 gracias").
_NOT_COUNTED = {
    "ademas", "ambas", "ambos", "apenas", "buenas", "cuantos", "das", "dos", "entonces", "gracias",
    "las", "llevas", "los", "mas", "menos", "nomas", "nos", "pagas", "pues", "quieres", "seis",
    "sus", "tienes", "tres", "tus", "unas", "unos",
}  # fmt: skip
# A number right after one of these (a colon or "#" between them is fine) is part of an address
# or a label of its own.
_LABELS = {
    "ref", "referencia", "sku", "nit", "cedula", "cc", "serie", "modelo", "version", "apto",
    "apartamento", "local", "oficina", "of", "piso", "interior", "int", "bodega", "calle", "cl",
    "carrera", "cra", "cr", "kr", "avenida", "av", "diagonal", "dg", "transversal", "tv", "km",
    "kilometro", "torre", "bloque", "casa", "manzana", "mz", "cp",
}  # fmt: skip
# A clause with one of these is about a code or a phone call: a plain number in it is no price.
_CODE_WORDS = {
    "codigo", "cod", "postal", "pedido", "orden", "reserva", "reservacion", "guia", "radicado",
    "ticket", "factura", "ahorros", "corriente", "consigna", "consignar", "bancolombia",
    "davivienda",
}  # fmt: skip
_PHONE_WORDS = {
    "llamanos", "llama", "llamar", "llamame", "telefono", "tel", "celular", "cel", "whatsapp", "wa",
    "linea", "numero", "nro", "nequi", "daviplata",
}  # fmt: skip
# ...unless it also has one of these: "Tu pedido vale 45000". "Envío" is left out, since a guide
# number has it ("Tu guía de envío es 7045123"), and so is what only leads to a code ("es", "son").
_PRICE_WORDS = {
    "precio", "precios", "vale", "valen", "valor", "cuesta", "cuestan", "costo", "costos", "total",
    "cobro", "cobramos", "tarifa", "sale", "salen", "queda", "quedan", "pagar", "pago", "pagas",
    "abono", "descuento", "dejo", "dejamos", "vendo", "vendemos", "doy", "damos",
}  # fmt: skip
# How far back such a word still speaks for the number.
_CODE_REACH = 7
_CLAUSE_END = {".", ",", "!", "?", ";"}
# Year words before a colon: "Año: 2025", while "Mochila: 2000" is a price.
_YEAR_WORDS = {"ano", "coleccion", "modelo", "temporada", "version", "edicion", "fabricacion"}
# Prepositions after which a year-like number is a price, not a year: "a 2000", ": 2000".
_PRICE_POSITION = {"a", ":"}


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold()).translate(_KEYCAP_MARKS)
    return "".join(char for char in plain if not unicodedata.combining(char))


def _normalized(text: str) -> str:
    """Folded text without links, phones, times and dates, with ``1'250.000`` and ``45 000`` as
    one number each."""
    folded = _LINK.sub(" ", _fold(_APOSTROPHE_MILLIONS.sub(".", text)))
    readable = _CODE.sub(" ", _TIME_OR_DATE.sub(" ", _PHONE.sub(" ", folded)))
    grouped = _SPACE_GROUPED.sub(lambda match: match.group().replace(" ", ""), readable)
    return _NUMBER_MARK.sub(r"\1 ", grouped)


def _number(token: str) -> tuple[float, bool]:
    """Value of a digit token and whether it carries Colombian thousands grouping."""
    if "." in token and "," in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return float(token.replace(thousands, "").replace(decimal, ".")), True
    for separator in ".,":
        if separator in token:
            head, *groups = token.split(separator)
            if len(groups) >= 2 and len(groups[-1]) == 2:  # "$85.000.00": cents
                groups = groups[:-1]
                token = token[: token.rfind(separator)]
            if all(len(group) == 3 for group in groups) and 1 <= len(head) <= 3:
                return float(token.replace(separator, "")), True
            return float(f"{head}.{''.join(groups)}"), False
    return float(token), False


def _words(tokens: list[str], start: int) -> tuple[int, int, bool]:
    """Parse spelled-out Spanish from ``start``: (value, tokens used, saw mil or millón)."""
    total, current, used, scaled = 0, 0, 0, False
    index = start
    while index < len(tokens):
        word = tokens[index]
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _HUNDREDS:
            current += _HUNDREDS[word]
        elif word in _THOUSAND:
            current = (current or 1) * 1000
            total, current, scaled = total + current, 0, True
        elif word in _MILLION:
            total = ((total + current) or 1) * 1_000_000
            current, scaled = 0, True
        elif (
            word in ("medio", "media") and index + 1 < len(tokens) and tokens[index + 1] in _MILLION
        ):
            total, scaled = total + current * 1_000_000 + 500_000, True  # "medio millón"
            current, index = 0, index + 1
        elif word == "y" and scaled and tokens[index + 1 : index + 2] == ["medio"]:
            total, index = total + 500_000, index + 1  # "un millón y medio"
        elif word == "y" and used and index + 1 < len(tokens) and tokens[index + 1] in _UNITS:
            pass  # "treinta y cinco"
        else:
            break
        index += 1
        used = index - start
    return total + current, used, scaled


def _price_cue(tokens: list[str], index: int) -> str | None:
    """'strong', 'weak', 'distant' or None for the price word before ``tokens[index]``."""
    position, crossed = index - 1, []
    while position >= 0 and tokens[position] in _CUE_LINKS and index - position <= 2:
        crossed.append(tokens[position])
        position -= 1
    word = tokens[position] if position >= 0 else ""
    # "quedan solo 3" counts stock; "queda en solo 85" is a price
    if word in _STRONG_CUES or (word in _EN_CUES and _EN_LINKS & set(crossed)):
        return "strong"
    if (
        word in _WEAK_CUES
        or word in _EN_CUES  # "te sale 85000"
        or (crossed and word in _EN_WEAK_CUES)  # "está en 85000"
        or "por" in crossed  # "2 por 85000"
    ):
        return "weak"
    # "el precio de la camisa es 85000": a strong word earlier in the same sentence
    for earlier in reversed(tokens[max(0, index - _CUE_REACH) : index]):
        if earlier in _SENTENCE_END or earlier[0].isdigit() or earlier == "$":
            break
        if earlier in _STRONG_CUES:
            return "distant"
    return None


def _palos(tokens: list[str], index: int) -> bool:
    """``dos palos`` are millions; ``dos palos de selfie`` are sticks, unless ``de pesos``."""
    if index >= len(tokens) or tokens[index] not in _MILLIONS_SLANG:
        return False
    return tokens[index + 1 : index + 2] != ["de"] or tokens[index + 2 : index + 3] == ["pesos"]


def _counts_something_else(tokens: list[str], index: int, value: float) -> bool:
    """``2 días``, ``2 a 3 días``, ``dos a tres días``, ``3 unidades``: a quantity, not a price.

    ``3 x $20.000`` counts, but ``85000 x unidad`` is a price per unit. A range of counts
    climbs, so a smaller number after this one starts something else: ``85000 y 3 cuotas``.
    """
    if value > 1 and tokens[index + 1 : index + 2] and tokens[index + 1] in _SINGULAR_UNITS:
        return False
    for token in tokens[index + 1 : index + 4]:
        if token in _NON_MONEY_UNITS:
            return token != "x" or value < 1000
        if token in _CONNECTORS:
            continue
        if token[0].isdigit():
            following = _number(token)[0]
        elif token in _UNITS or token in _HUNDREDS:
            following = _UNITS.get(token, 0) + _HUNDREDS.get(token, 0)
        else:
            return False
        if following < value:
            return False
    return False


def _currency_follows(after: list[str]) -> bool:
    """``20 pesos``, ``20 USD``, ``20 de pesos``, ``20$``, but ``talla 38 $85.000`` marks the 85."""
    if not after:
        return False
    if after[0] in ("$", "€"):
        return len(after) == 1 or not after[1][0].isdigit()
    return after[0] in _CURRENCY_AFTER or after[0] in _FOREIGN_AFTER or after[:2] == ["de", "pesos"]


def _counted_by_noun(tokens: list[str], index: int) -> bool:
    """``1000 mochilas``: a plural noun right after a plain number says what it counts."""
    word = tokens[index + 1] if index + 1 < len(tokens) else ""
    if word == "en" and tokens[index + 2 : index + 3] and tokens[index + 2] in _STOCK_PLACES:
        return True
    return word.isalpha() and word.endswith("s") and word not in _NOT_COUNTED


def _labelled(tokens: list[str], index: int) -> bool:
    """``Ref 2310``, ``Ref: 2310``, ``Of 1204``, ``#1234``, ``# 45-1020``: a code or an address."""
    last = tokens[index - 1] if index >= 1 else ""
    prior = tokens[index - 2] if index >= 2 else ""
    if last == "#":
        return True
    if last in ("-", "–") and index >= 3 and tokens[index - 3] == "#" and prior[0].isdigit():
        return True
    return prior in _LABELS if last == ":" else last in _LABELS


def _about_a_code(tokens: list[str], index: int) -> bool:
    """``Tu código de reserva es 58213``, ``Llámanos al 444 1234``: the clause says what the number
    is, unless it has a price word too or puts the number where a price goes (``pedido a 45000``)."""
    start = index
    while start > 0 and index - start < _CODE_REACH and tokens[start - 1] not in _CLAUSE_END:
        start -= 1
    end = index
    while end + 1 < len(tokens) and tokens[end + 1] not in _CLAUSE_END:
        end += 1
    if _PRICE_WORDS & set(tokens[start : end + 1]):
        return False
    before = set(tokens[start:index])
    if before & _PHONE_WORDS:
        return True
    return bool(before & _CODE_WORDS) and tokens[index - 1] not in ("a", "por")


def _is_year(tokens: list[str], index: int, token: str, value: float) -> bool:
    """``Desde 2015``, ``de 2019 a 2024``, ``Año: 2025``, but ``a 2000`` and ``Mochila: 2000`` are
    prices."""
    if not (token.isdigit() and len(token) == 4 and 1900 <= value <= 2099):
        return False
    before = tokens[index - 1] if index else ""
    if before == ":" and index >= 2 and tokens[index - 2] in _YEAR_WORDS:
        return True
    return before not in _PRICE_POSITION or (index >= 2 and tokens[index - 2][0].isdigit())


def _remainder(tokens: list[str], index: int, below: int) -> tuple[int, int]:
    """What follows an amount with ``mil`` or ``millón``, as in ``45 mil 900`` or ``1 millón 500
    mil``: (value, tokens used), with value under ``below``; (0, 0) when nothing does."""
    if index >= len(tokens):
        return 0, 0
    token = tokens[index]
    if below == 1_000_000 and tokens[index : index + 2] == ["y", "medio"]:
        return 500_000, 2
    if token.isdigit() and len(token) <= 3 and not _counts_something_else(tokens, index, 0):
        nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
        if below == 1000 and (nxt in _THOUSAND or nxt in _THOUSANDS_SLANG or nxt == "k"):
            return 0, 0  # "45 mil 50 mil": the next amount, not the rest of this one
        value, used = int(token), 1
        if below == 1_000_000 and tokens[index + 1 : index + 2] == ["mil"]:
            more, extra = _remainder(tokens, index + 2, 1000)
            value, used = value * 1000 + more, 2 + extra
        return (value, used) if value < below else (0, 0)
    if token in _UNITS or token in _HUNDREDS:
        value, used, scaled = _words(tokens, index)
        after = tokens[index + used : index + used + 1]
        if after and after[0] in _NON_MONEY_UNITS:
            return 0, 0
        if value < below and (below == 1_000_000 or not scaled):
            return value, used
    return 0, 0


def amounts_cop(text: str) -> list[int]:
    """Every monetary amount in what the shop says, as integer pesos, in order of appearance."""
    return _read(text, fail_closed=True)


def marked_amounts(text: str) -> list[int]:
    """The amounts in what a customer wrote or in a bare data value: only what is marked or cued
    as money, so "Mi pedido es el 58213" and ``sku: 45000`` hold none."""
    return _read(text, fail_closed=False)


def _read(text: str, *, fail_closed: bool) -> list[int]:
    tokens = _TOKEN.findall(_normalized(text))
    found: list[int] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        before = tokens[max(0, index - 3) : index]
        marked_before = bool(before) and before[-1] in _MARK_BEFORE
        if token[0].isdigit():
            value, grouped = _number(token)
            following = tokens[index + 1 : index + 4]
            nxt = following[0] if following else ""
            if nxt == "%":  # percentages are offers, not amounts (vitrina_core.offers)
                index += 1
                continue
            multiplier, consumed, remainder = 1, 1, 0
            if nxt in _THOUSAND or nxt in _THOUSANDS_SLANG or nxt == "k":
                multiplier, consumed = 1000, 2
            elif nxt in _MILLION or _palos(tokens, index + 1):
                multiplier, consumed = 1_000_000, 2
            elif nxt in _CONNECTORS and len(following) >= 3 and following[1][0].isdigit():
                # "entre 12 y 15 mil": the multiplier after the second number applies to both
                if following[2] in _THOUSAND or following[2] in _THOUSANDS_SLANG:
                    multiplier = 1000
                elif following[2] in _MILLION or _palos(tokens, index + 3):
                    multiplier = 1_000_000
            if consumed == 2:  # "45 mil 900", "1 millón 500 mil": the rest of the amount
                remainder, used = _remainder(tokens, index + 2, multiplier)
                consumed += used
            after = tokens[index + consumed : index + consumed + 2]
            currency_after = _currency_follows(after)
            # Colombian numbers: 10 digits starting with 3 (mobile) or 60 (landline), or +57 prefixed
            phone_like = token.isdigit() and (
                len(token) >= 11 or (len(token) == 10 and token[0] in "36")
            )
            cue = _price_cue(tokens, index)
            counts = _counts_something_else(tokens, index, value)
            if counts and cue == "strong" and nxt in _LETTER_UNITS:
                counts = False
            not_a_price = (
                counts or _labelled(tokens, index) or (not grouped and _about_a_code(tokens, index))
            )
            cue = None if not_a_price else cue
            year_like = not grouped and _is_year(tokens, index, token, value)
            # Fail closed: from 1.000 up it is money unless it clearly is something else.
            something_else = (
                not_a_price
                or year_like
                or (not grouped and _counted_by_noun(tokens, index))
                or (token.isdigit() and token[0] == "0")
            )
            is_money = (
                marked_before
                or multiplier > 1
                or currency_after
                or (cue == "strong" and not phone_like)
                or (cue == "weak" and value >= 1000 and not phone_like)
                or (cue == "distant" and value >= 1000 and not phone_like and not year_like)
                or (grouped and value >= 1000 and not not_a_price)
                or (fail_closed and value >= 1000 and not phone_like and not something_else)
            )
            if is_money:
                # round first: 4.1 * 1_000_000 is 4099999.999… in binary
                found.append(int(round(value * multiplier, 6)) + remainder)
            index += consumed
            continue
        half_million = token in ("medio", "media") and tokens[index + 1 : index + 2] in (
            ["millon"],
            ["millones"],
        )
        if (
            token in _UNITS
            or token in _HUNDREDS
            or token in _THOUSAND
            or token in _MILLION
            or half_million
        ):
            value, used, scaled = _words(tokens, index)
            if used:
                end = index + used
                after = tokens[end : end + 2]
                slang = bool(after) and after[0] in _THOUSANDS_SLANG
                currency_after = _currency_follows(after)
                if slang:
                    found.append(value * 1000)
                    index += used + 1
                    continue
                if not scaled and _palos(tokens, end):  # "dos palos y medio"
                    rest, extra = _remainder(tokens, end + 1, 1_000_000)
                    found.append(value * 1_000_000 + rest)
                    index = end + 1 + extra
                    continue
                # "¡mil gracias!": a bare "mil" is money only with a currency word or a $ sign
                lone_mil = used == 1 and token == "mil"
                # "cuesta noventa" is reported as written, like "vale 85"
                priced = (
                    not (used == 1 and token in _ARTICLES)
                    and not _counts_something_else(tokens, end - 1, value)
                    and _price_cue(tokens, index) == "strong"
                )
                if (scaled and not lone_mil) or currency_after or marked_before or priced:
                    found.append(value)
                index += used
                continue
        index += 1
    return found


def format_cop(amount: int) -> str:
    """Colombian notation: 85000 -> $85.000."""
    return "$" + f"{amount:,}".replace(",", ".")
