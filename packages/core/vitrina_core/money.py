"""Spanish money expressions to integer COP (FR-026, FR-085; constitution Art. III.2).

One normalizer for both sides of the price check: catalog extracts ``amounts_cop`` when a
fact or product is written, and the price validator extracts the amounts in a reply. If
the two parsed differently, honest replies would fail and invented prices could pass.

Counted as money: a number with ``$``, ``COP``, ``pesos`` or ``lucas``; a number with a
multiplier (``mil``, ``k``, ``millón``); spelled-out amounts with ``mil``/``millón`` or a
currency word; numbers grouped like ``12.000``; and a plain number right after a price word
(``cuesta 85000``). Not money: percentages, phone numbers, dates, times, sizes, counts.
"""

import re
import unicodedata

_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-zñ]+|[$%/:]")

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
# Price words before a plain number. Strong: any size counts, so a short "vale 85" is reported
# and fails the check instead of hiding a price. Via "en": "te queda en 85". Weak: from 1.000 up.
_STRONG_CUES = {
    "precio", "precios", "vale", "valen", "valor", "cuesta", "cuestan", "costo", "costos",
    "total", "envio", "domicilio", "cobro", "cobramos", "tarifa",
}  # fmt: skip
_EN_CUES = {"queda", "quedaria", "sale", "saldria", "deja", "dejo"}
_WEAK_CUES = {"son", "pagar", "pago", "pagas", "abono", "descuento"}
_CUE_LINKS = {"en", "de", "es", "a", "por", ":"}
_CONNECTORS = {"y", "a", "o"}
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
            total = (total + (current or 1)) * 1_000_000
            current, scaled = 0, True
        elif word == "y" and used and index + 1 < len(tokens) and tokens[index + 1] in _UNITS:
            pass  # "treinta y cinco"
        else:
            break
        index += 1
        used = index - start
    return total + current, used, scaled


def _price_cue(tokens: list[str], index: int) -> str | None:
    """'strong', 'weak' or None for the price word just before ``tokens[index]``."""
    position, crossed = index - 1, False
    while position >= 0 and tokens[position] in _CUE_LINKS and index - position <= 2:
        crossed, position = True, position - 1
    word = tokens[position] if position >= 0 else ""
    if word in _STRONG_CUES or (crossed and word in _EN_CUES):
        return "strong"
    return "weak" if word in _WEAK_CUES else None


def _counts_something_else(tokens: list[str], index: int) -> bool:
    """``2 días``, ``2 a 3 días``, ``3 unidades``: the number is a quantity, not a price."""
    for token in tokens[index + 1 : index + 4]:
        if token in _NON_MONEY_UNITS:
            return True
        if not (token[0].isdigit() or token in _CONNECTORS):
            return False
    return False


def amounts_cop(text: str) -> list[int]:
    """Every monetary amount in ``text``, as integer pesos, in order of appearance."""
    tokens = _TOKEN.findall(_fold(text))
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
            multiplier, consumed = 1, 1
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
            after = tokens[index + consumed : index + consumed + 2]
            currency_after = bool(after) and (
                after[0] in _CURRENCY_AFTER or after[:2] == ["de", "pesos"]
            )
            # Colombian numbers: 10 digits starting with 3 (mobile) or 60 (landline), or +57 prefixed
            phone_like = token.isdigit() and (
                len(token) >= 11 or (len(token) == 10 and token[0] in "36")
            )
            cue = None if _counts_something_else(tokens, index) else _price_cue(tokens, index)
            is_money = (
                marked_before
                or multiplier > 1
                or currency_after
                or (grouped and value >= 1000)
                or (cue == "strong" and not phone_like)
                or (cue == "weak" and value >= 1000 and not phone_like)
            )
            if is_money:
                found.append(int(value * multiplier))
            index += consumed
            continue
        if token in _UNITS or token in _HUNDREDS or token in _THOUSAND or token in _MILLION:
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
