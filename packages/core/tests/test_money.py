import pytest
from hypothesis import given
from hypothesis import strategies as st

from vitrina_core.money import amounts_cop, format_cop

UNITS = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
TEENS = [
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
]
TWENTIES = ["veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis",
            "veintisiete", "veintiocho", "veintinueve"]  # fmt: skip
TENS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
HUNDREDS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos",
            "setecientos", "ochocientos", "novecientos"]  # fmt: skip


def words_below_1000(n: int) -> str:
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    parts = [HUNDREDS[hundreds]] if hundreds else []
    if rest >= 30:
        tens, unit = divmod(rest, 10)
        parts.append(TENS[tens] + (f" y {UNITS[unit]}" if unit else ""))
    elif rest >= 20:
        parts.append(TWENTIES[rest - 20])
    elif rest >= 10:
        parts.append(TEENS[rest - 10])
    elif rest:
        parts.append(UNITS[rest])
    return " ".join(parts)


def spelled(n: int) -> str:
    """Spanish words for n, the way a seller would write a price (test oracle)."""
    millions, rest = divmod(n, 1_000_000)
    thousands, units = divmod(rest, 1000)
    parts = []
    if millions:
        parts.append(
            "un millón"
            if millions == 1
            else f"{words_below_1000(millions)} millones".replace("uno millones", "un millones")
        )
    if thousands:
        parts.append(
            "mil"
            if thousands == 1
            else f"{words_below_1000(thousands)} mil".replace("uno mil", "un mil")
        )
    if units:
        parts.append(words_below_1000(units))
    return " ".join(parts)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$12.000", [12000]),
        ("$ 12.000", [12000]),
        ("12 mil", [12000]),
        ("12mil", [12000]),
        ("12k", [12000]),
        ("doce mil pesos", [12000]),
        ("COP 12000", [12000]),
        ("COP$12.000", [12000]),
        ("12000 COP", [12000]),
        ("$12.000 COP", [12000]),
        ("12.000 pesos", [12000]),
        ("1.200.000", [1200000]),
        ("$1,5 millones", [1500000]),
        ("1.5 millones de pesos", [1500000]),
        ("un millón", [1000000]),
        ("dos millones quinientos mil", [2500000]),
        ("20 lucas", [20000]),
        ("una luca", [1000]),
        ("mil quinientos pesos", [1500]),
        ("quince mil", [15000]),
        ("veinticinco mil", [25000]),
        ("doscientos cincuenta mil", [250000]),
        ("$12.000,50", [12000]),
        ("12.5 mil", [12500]),
        ("$12,000", [12000]),
        ("cien mil pesos", [100000]),
        ("ciento veinte mil", [120000]),
    ],
)
def test_fr_026_spanish_money_expressions_normalize_to_integer_cop(
    text: str, expected: list[int]
) -> None:
    assert amounts_cop(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Envío a Medellín $12.000, 2–3 días", [12000]),
        ("El bolso vale $85.000 y el envío $12.000", [85000, 12000]),
        ("Cuesta 85000", [85000]),
        ("Precio: 85000 y envío 12000", [85000, 12000]),
        ("Te sale entre 12 y 15 mil", [12000, 15000]),
        ("Te queda en $85.000 COP con envío gratis", [85000]),
        ("Son 3 x $20.000 = $60.000", [20000, 60000]),
        # A short number right after a price word is reported as written: it cannot match a
        # cited $85.000, so the validator asks for the full amount instead of letting it pass.
        ("Vale 85", [85]),
        ("Te queda en 85", [85]),
        ("El precio es de 85", [85]),
        # The same in words: "cuesta noventa" is a price with its "mil" left out
        ("Cuesta noventa", [90]),
        ("Vale ochenta y cinco", [85]),
        ("Te la dejo en noventa", [90]),
        ("Cuesta mil", [1000]),
    ],
)
def test_fr_026_amounts_are_found_in_replies(text: str, expected: list[int]) -> None:
    assert amounts_cop(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A plain number after a price word, with or without the words in between
        ("Está en 95000", [95000]),
        ("Te sale 95000", [95000]),
        ("Salen a 95000", [95000]),
        ("Te quedan en 95000", [95000]),
        ("Serían 95000", [95000]),
        ("Te lo dejo a solo 95000", [95000]),
        ("Dos por 95000", [95000]),
        ("Cuesta unos 95000", [95000]),
        ("Precio - 85000", [85000]),
        ("Precio 85000 x unidad", [85000]),
        ("El precio de la camisa es 95000", [95000]),
        ("Envío Bogotá 8000", [8000]),
        ("Envío a Bogotá: 8000", [8000]),
        # Amounts in parts
        ("45 mil 900", [45900]),
        ("45 mil quinientos", [45500]),
        ("1 millón 500 mil", [1500000]),
        ("1 millón y medio", [1500000]),
        ("medio millón", [500000]),
        ("un millón y medio", [1500000]),
        ("dos millones y medio de pesos", [2500000]),
        ("mil millones", [1000000000]),
        # "palos" are millions, right after a number
        ("dos palos", [2000000]),
        ("Te sale en 2 palos", [2000000]),
        ("Te queda en un palo", [1000000]),
        ("dos palos y medio", [2500000]),
        ("Son dos palos de pesos", [2000000]),
        # A count after a price is not a range with it
        ("Precio 85000 y 3 cuotas", [85000]),
        ("Cuesta 85000 y 2 unidades", [85000]),
        # Other groupings, ranges and currencies
        ("$1'250.000", [1250000]),
        ("1’250.000", [1250000]),
        ("$ 45 000", [45000]),
        ("45 000 pesos", [45000]),
        ("45\u00a0000 pesos", [45000]),
        ("Entre 40-45 mil", [40000, 45000]),
        ("Entre 40 – 45 mil", [40000, 45000]),
        ("De 40 a 45 mil", [40000, 45000]),
        ("20 USD", [20]),
        ("12 mil 3 cuotas", [12000]),
        ("45 mil tres cuotas", [45000]),
        ("45 mil 900 pesos", [45900]),
        ("1 millón 250 mil 500", [1250500]),
        # A list keeps its amounts apart, with commas or without
        ("Las camisas valen 45 mil, 50 mil y 60 mil", [45000, 50000, 60000]),
        ("45 mil 50 mil", [45000, 50000]),
        ("45 mil, 900 pesos", [45000, 900]),
        ("1 millón, 500 mil", [1000000, 500000]),
        # A price word before a comma, and a marked amount that has its own
        ("El precio es, como siempre, 85000", [85000]),
        ("Precio $85.000 y 3500 seguidores", [85000]),
        # Years are left out only a few words after a price word
        ("El precio de la edición es 2099", []),
        ("El precio de la edición es 2100", [2100]),
        ("El precio de la edición es 1899", [1899]),
    ],
)
def test_fr_026_colombian_ways_of_writing_a_price_are_read_whole(
    text: str, expected: list[int]
) -> None:
    assert amounts_cop(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Llámanos al 300 000 0001",
        "Escríbenos al 3000000001",
        "Tenemos 3 unidades en talla 38",
        "Hay 20% de descuento",
        "El 27/10 abrimos a las 2:30",
        "Entregamos en 2 a 3 días hábiles",
        "Referencia 2026, color rojo",
        "Dos bolsos y tres gorras",
        "Envío gratis en Bogotá",
        "¡Mil gracias por tu compra!",
        "Vale, son 3 unidades",
        "Queda 1 unidad en talla M",
        "Cien por ciento algodón",
        "Entregamos en 2-3 días",
        "Por 3 meses de garantía",
        "Calle 45 # 12-30",
        "Tallas 36 38 40",
        "Media hora y medio kilo",
        "Envío en 2 a 3 días",
        # A year a few words after a price word is a date, not a price
        "El precio de lanzamiento sube en 2026",
        "El envío es gratis. Tenemos 3500 seguidores",
        # An article after a price word, a count in words, and sticks that are sticks
        "Cuesta un poco más",
        "Vale una fortuna",
        "Envío en dos días",
        "Envío: dos a tres días",
        "El palo de escoba",
        "Un palo de escoba",
        "Tenemos 2 palos de selfie",
    ],
)
def test_fr_026_numbers_that_are_not_money_are_ignored(text: str) -> None:
    assert amounts_cop(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A plain number from 1.000 up is money, with or without a word that says so
        ("La mochila te la vendo a 45000.", [45000]),
        ("Mochila: 45000", [45000]),
        ("Mochila 45000 azul", [45000]),
        ("Mochila 45000 gracias", [45000]),
        ("Mochila-45000", [45000]),
        ("COP85000", [85000]),
        ("Vale45000", [45000]),
        ("Tu pedido: 45000", [45000]),
        ("Te queda en solo 85", [85]),
        ("Hoy 1500", [1500]),
        ("Combo 2:45000", [45000]),
        # A short number after a selling word, as a price word does
        ("Te la vendo a 45", [45]),
        ("Te la doy en cuarenta y cinco", [45]),
        # A colon or a slash between numbers is not always a time or a date
        ("Talla 38: 85 mil, combo 2: 45.000", [85000, 45000]),
        ("Talla 38: 45000", [45000]),
        ("$85.000/unidad", [85000]),
        # Other currencies, before or after the number, in digits or in words
        ("La mochila vale USD 20.", [20]),
        ("US$ 20", [20]),
        ("EUR 20", [20]),
        ("€20", [20]),
        ("20€", [20]),
        ("20$", [20]),
        ("Talla 38 $85.000", [85000]),
        ("Llevo 2 $85.000", [85000]),
        ("20 dólares", [20]),
        ("Son veinte dólares", [20]),
        ("veinte euros", [20]),
        # A year-like number where a price goes
        ("Mochila: 2000", [2000]),
        ("Empanadas a 2000", [2000]),
        ("Vale 2000", [2000]),
        # A decimal with a multiplier keeps its exact value
        ("4.1 millones", [4100000]),
        ("4,1 millones", [4100000]),
        ("1.1 millones", [1100000]),
        ("2,3 mil", [2300]),
        ("$12.000,75", [12000]),
    ],
)
def test_as_3_4_a_plain_number_from_a_thousand_up_counts_as_money(
    text: str, expected: list[int]
) -> None:
    assert amounts_cop(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Phone numbers (synthetic), however they are grouped
        "Escríbenos al 3000000101",
        "Escríbenos al +573000000101",
        "Escríbenos al +57 300 000 0101",
        "Llama al 300 000 0101",
        "Llama al 300-000-0101",
        "Llama al 300 000 01 01",
        "Línea 601 000 0101",
        "Tel: 6040101",
        "Línea gratuita 018000 000101",
        # Addresses
        "Calle 100 # 15-20",
        "Carrera 7 # 45-1020",
        "Estamos en el local 1204",
        "Apto 1502, torre 2",
        "Oficina: 1001",
        "Of. 1001",
        "Avenida 1500 con 30",
        # Hours and dates
        "Abrimos 8:00 a 18:00",
        "De 9:00 a. m. a 6:00 p. m.",
        "Llega el 27/10/2026",
        "Hasta 12/2026",
        # Years
        "Desde 2015 tejemos mochilas",
        "Colección 2026",
        "De 2019 a 2024",
        "Fundada en 1998",
        # Units and quantities
        "Batería de 5000 mAh",
        "Bolso de 1500 gramos",
        "Termo de 1200 ml",
        "Hilo de 2000 metros",
        "Monitor 1080p",
        "Motor de 1500 W",
        "Tenemos 1000 unidades",
        "Tenemos 1.000 unidades",
        "Necesito 1000 mochilas",
        "Más de 2000 clientes",
        "Sábanas de 1000 hilos",
        # Codes
        "Referencia 2310",
        "Ref: 2310",
        "Tu pedido #1234 va en camino",
        "Tu pedido 12345 va en camino",
        "Tu código es ABC1234",
        "Reloj XR2000",
        # Stock is no price, even after a word that can introduce one
        "Quedan solo 3",
        "Te quedan apenas 2",
        "Guía 123456",
        "NIT 900000101",
    ],
)
def test_as_3_4_numbers_that_clearly_are_not_money_stay_out(text: str) -> None:
    assert amounts_cop(text) == []


@given(st.integers(min_value=1000, max_value=50_000_000))
def test_fr_026_colombian_digit_formats_round_trip(amount: int) -> None:
    grouped = format_cop(amount)

    assert amounts_cop(grouped) == [amount]
    assert amounts_cop(f"{amount} pesos") == [amount]
    assert amounts_cop(f"Precio {amount}") == [amount]
    assert amounts_cop(f"{grouped[1:]} COP") == [amount]


@given(st.integers(min_value=1, max_value=99_999).map(lambda n: n * 1000))
def test_fr_026_thousands_multipliers_round_trip(amount: int) -> None:
    thousands = amount // 1000

    assert amounts_cop(f"{thousands} mil") == [amount]
    assert amounts_cop(f"{thousands}k") == [amount]
    assert amounts_cop(f"{thousands} lucas") == [amount]


@given(st.integers(min_value=1000, max_value=999_999_999))
def test_fr_026_spelled_out_prices_round_trip(amount: int) -> None:
    assert amounts_cop(f"{spelled(amount)} pesos") == [amount]


def test_format_cop_uses_colombian_grouping() -> None:
    assert [format_cop(n) for n in (0, 999, 12000, 1200000)] == [
        "$0",
        "$999",
        "$12.000",
        "$1.200.000",
    ]


@given(st.integers(min_value=1_000_000, max_value=999_999_999))
def test_fr_026_an_apostrophe_marks_the_millions(amount: int) -> None:
    millions, rest = divmod(amount, 1_000_000)
    thousands, units = divmod(rest, 1000)
    assert amounts_cop(f"${millions}'{thousands:03d}.{units:03d}") == [amount]


@given(st.integers(min_value=1000, max_value=50_000_000))
def test_fr_026_space_grouped_thousands_with_a_money_mark(amount: int) -> None:
    spaced = f"{amount:,}".replace(",", " ")
    assert amounts_cop(f"{spaced} pesos") == [amount]
    assert amounts_cop(f"$ {spaced}") == [amount]


@given(st.integers(min_value=1, max_value=999), st.integers(min_value=1, max_value=999))
def test_fr_026_thousands_and_the_rest_are_one_amount(thousands: int, rest: int) -> None:
    assert amounts_cop(f"{thousands} mil {rest}") == [thousands * 1000 + rest]


@given(
    st.integers(min_value=1, max_value=998).flatmap(
        lambda low: st.tuples(st.just(low), st.integers(min_value=low + 1, max_value=999))
    )
)
def test_fr_026_a_range_shares_its_multiplier(ends: tuple[int, int]) -> None:
    low, high = ends
    assert amounts_cop(f"entre {low}-{high} mil") == [low * 1000, high * 1000]


@pytest.mark.parametrize(
    "phrase",
    ["Está en {}", "Te sale {}", "Por {}", "Cuesta unos {}", "Te lo dejo a solo {}",
     "El precio de la camisa es {}"],
)  # fmt: skip
@given(amount=st.integers(min_value=1000, max_value=50_000_000))
def test_fr_026_a_plain_amount_after_a_price_word_is_found(phrase: str, amount: int) -> None:
    # A year (1900 to 2099) a few words after a price word is left out: it is a date there.
    distant = phrase.startswith("El precio")
    expected = [] if distant and 1900 <= amount <= 2099 else [amount]
    assert amounts_cop(phrase.format(amount)) == expected


@given(st.integers(min_value=2, max_value=999))
def test_fr_026_a_spelled_amount_after_a_price_word_is_found(amount: int) -> None:
    assert amounts_cop(f"Cuesta {words_below_1000(amount)}") == [amount]


@given(st.integers(min_value=1, max_value=999))
def test_fr_026_palos_after_a_number_are_millions(millions: int) -> None:
    assert amounts_cop(f"{millions} palos") == [millions * 1_000_000]


@given(st.lists(st.integers(min_value=1, max_value=999), min_size=2, max_size=4))
def test_fr_026_a_list_of_prices_keeps_each_amount(thousands: list[int]) -> None:
    *first, last = [f"{amount} mil" for amount in thousands]
    assert amounts_cop(f"Valen {', '.join(first)} y {last}") == [n * 1000 for n in thousands]
