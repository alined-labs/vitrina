"""Spanish money expressions to integer COP (FR-026, FR-085; constitution Art. III.2).

One normalizer for both sides of the price check: catalog extracts ``amounts_cop`` when a
fact or product is written, and the price validator extracts the amounts in a reply. If
the two parsed differently, honest replies would fail and invented prices could pass.

Counted as money: a number with ``$``, ``COP``, ``pesos`` or ``lucas``; a number with a
multiplier (``mil``, ``k``, ``millón``); spelled-out amounts with ``mil``/``millón`` or a
currency word; numbers grouped like ``12.000``, ``1'250.000`` or ``45 000``; a plain number
right after a price word (``cuesta 85000``, ``te sale 85000``, ``por 85000``); a plain number
from 1.000 up a few words after one (``el precio de la camisa es 85000``); and an amount in
another currency (``20 USD``), which no cited peso amount matches. ``45 mil 900`` is 45.900,
``medio millón`` 500.000 and ``40-45 mil`` both ends. Not money: percentages, phone numbers,
dates, times, years, sizes, counts.
"""

import re
import unicodedata

# A comma or full stop not inside a number is kept: "45 mil, 50 mil" is two amounts.
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-zñ]+|[$%/:\-–—]|[.,!?;](?!\d)")
# 1'250.000: an apostrophe marks the millions. 45 000: a space groups thousands, as some
# keyboards and writers do; the number is then read as written, so it needs a money mark.
_APOSTROPHE_MILLIONS = re.compile(r"(?<=\d)['’´`](?=\d{3}(?:[.,]\d{3})*(?!\d))")
_SPACE_GROUPED = re.compile(r"(?<![\d.,])\d{1,3}(?: \d{3})+(?![\d.,]| \d)")

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
_THOUSANDS_SLANG = {"luca", "lucas", "luka", "lukas"}
_CURRENCY_AFTER = {"pesos", "peso", "pesitos", "pesito", "cop"}
# Another currency: reported as written, so it can only fail the check.
_FOREIGN_AFTER = {"usd", "dolar", "dolares", "eur", "euro", "euros"}
# Price words before a plain number. Strong: any size counts, so a short "vale 85" is reported
# and fails the check instead of hiding a price. Via "en": "te queda en 85", while "te queda 85"
# and "está en 85" are weak. Weak: from 1.000 up, and so is a strong word a few words before.
_STRONG_CUES = {
    "precio", "precios", "vale", "valen", "valor", "cuesta", "cuestan", "costo", "costos",
    "total", "envio", "domicilio", "cobro", "cobramos", "tarifa",
}  # fmt: skip
_EN_CUES = {
    "queda", "quedan", "quedaria", "quedarian", "sale", "salen", "saldria", "saldrian", "deja",
    "dejan", "dejo", "dejamos",
}  # fmt: skip
_EN_WEAK_CUES = {"esta", "estan", "estaria"}
_WEAK_CUES = {"son", "seria", "serian", "sera", "seran", "pagar", "pago", "pagas", "abono",
              "descuento"}  # fmt: skip
_CUE_LINKS = {
    "en", "de", "es", "a", "por", ":", "-", "–", "—", "unos", "unas", "solo", "solamente",
    "apenas", "como", "aprox", "aproximadamente",
}  # fmt: skip
# How far back a strong price word still makes a plain number from 1.000 up a price.
_CUE_REACH = 6
_SENTENCE_END = {".", "!", "?", ";"}
_CONNECTORS = {"y", "a", "o", "-", "–", "—"}
# A number followed by one of these counts something else.
_NON_MONEY_UNITS = {
    "dia", "dias", "hora", "horas", "semana", "semanas", "mes", "meses", "minuto", "minutos",
    "ano", "anos", "unidad", "unidades", "talla", "tallas", "cm", "metro", "metros", "kg",
    "kilo", "kilos", "gramos", "gr", "litro", "litros", "ml", "persona", "personas", "pieza",
    "piezas", "par", "pares", "cuota", "cuotas", "x",
}  # fmt: skip


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in plain if not unicodedata.combining(char) or char == "̃")


def _normalized(text: str) -> str:
    """Folded text with ``1'250.000`` and ``45 000`` rewritten as one number each."""
    folded = _fold(_APOSTROPHE_MILLIONS.sub(".", text))
    return _SPACE_GROUPED.sub(lambda match: match.group().replace(" ", ""), folded)


def _number(token: str) -> tuple[float, bool]:
    """Value of a digit token and whether it carries Colombian thousands grouping."""
    if "." in token and "," in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return float(token.replace(thousands, "").replace(decimal, ".")), True
    for separator in ".,":
        if separator in token:
            head, *groups = token.split(separator)
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
    if word in _STRONG_CUES or (crossed and word in _EN_CUES):
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


def _counts_something_else(tokens: list[str], index: int, value: float) -> bool:
    """``2 días``, ``2 a 3 días``, ``3 unidades``: the number is a quantity, not a price.

    ``3 x $20.000`` counts, but ``85000 x unidad`` is a price per unit.
    """
    for token in tokens[index + 1 : index + 4]:
        if token in _NON_MONEY_UNITS:
            return token != "x" or value < 1000
        if not (token[0].isdigit() or token in _CONNECTORS):
            return False
    return False


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
    """Every monetary amount in ``text``, as integer pesos, in order of appearance."""
    tokens = _TOKEN.findall(_normalized(text))
    found: list[int] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        before = tokens[max(0, index - 3) : index]
        marked_before = bool(before) and (before[-1] == "$" or before[-1] == "cop")
        if token[0].isdigit():
            value, grouped = _number(token)
            following = tokens[index + 1 : index + 4]
            nxt = following[0] if following else ""
            # percentages, dates (27/10) and times (2:30) are never money
            after_clock = (
                index >= 2 and tokens[index - 1] in ("/", ":") and tokens[index - 2][0].isdigit()
            )
            if (
                nxt == "%"
                or (nxt in ("/", ":") and len(following) > 1 and following[1][0].isdigit())
                or after_clock
            ):
                index += 1
                continue
            multiplier, consumed, remainder = 1, 1, 0
            if nxt in _THOUSAND or nxt in _THOUSANDS_SLANG or nxt == "k":
                multiplier, consumed = 1000, 2
            elif nxt in _MILLION:
                multiplier, consumed = 1_000_000, 2
            elif nxt in _CONNECTORS and len(following) >= 3 and following[1][0].isdigit():
                # "entre 12 y 15 mil": the multiplier after the second number applies to both
                if following[2] in _THOUSAND or following[2] in _THOUSANDS_SLANG:
                    multiplier = 1000
                elif following[2] in _MILLION:
                    multiplier = 1_000_000
            if consumed == 2:  # "45 mil 900", "1 millón 500 mil": the rest of the amount
                remainder, used = _remainder(tokens, index + 2, multiplier)
                consumed += used
            after = tokens[index + consumed : index + consumed + 2]
            currency_after = bool(after) and (
                after[0] in _CURRENCY_AFTER
                or after[0] in _FOREIGN_AFTER
                or after[:2] == ["de", "pesos"]
            )
            # Colombian numbers: 10 digits starting with 3 (mobile) or 60 (landline), or +57 prefixed
            phone_like = token.isdigit() and (
                len(token) >= 11 or (len(token) == 10 and token[0] in "36")
            )
            year_like = token.isdigit() and len(token) == 4 and 1900 <= value <= 2099
            counts = _counts_something_else(tokens, index, value)
            cue = None if counts else _price_cue(tokens, index)
            is_money = (
                marked_before
                or multiplier > 1
                or currency_after
                or (grouped and value >= 1000)
                or (cue == "strong" and not phone_like)
                or (cue == "weak" and value >= 1000 and not phone_like)
                or (cue == "distant" and value >= 1000 and not phone_like and not year_like)
            )
            if is_money:
                found.append(int(value * multiplier) + remainder)
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
                after = tokens[index + used : index + used + 2]
                slang = bool(after) and after[0] in _THOUSANDS_SLANG
                currency_after = bool(after) and (
                    after[0] in _CURRENCY_AFTER or after[:2] == ["de", "pesos"]
                )
                if slang:
                    found.append(value * 1000)
                    index += used + 1
                    continue
                # "¡mil gracias!": a bare "mil" is money only with a currency word or a $ sign
                lone_mil = used == 1 and token == "mil"
                if (scaled and not lone_mil) or currency_after or marked_before:
                    found.append(value)
                index += used
                continue
        index += 1
    return found


def format_cop(amount: int) -> str:
    """Colombian notation: 85000 -> $85.000."""
    return "$" + f"{amount:,}".replace(",", ".")
