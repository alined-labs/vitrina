"""Promises about a deal need a source that states them (AS-3.4; constitution Art. III.2)."""

import pytest

from vitrina_core.offers import offer_claims, promotions, unsupported_offers


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Te hago 30 % de descuento", "30 %"),
        ("Te hago 30% de descuento", "30 %"),
        ("Hoy 20 % en mochilas", "20 %"),
        ("Tienes el 15 por ciento menos", "15 %"),
        ("Te hago el veinte por ciento", "20 %"),
        ("¿Te sirve con 30 % de descuento?", "30 %"),
        ("Si pagas de contado te hago 10 %", "10 %"),
        ("No dudes en aprovechar el 30 %", "30 %"),
        ("Tenemos 2x1 en mochilas", "2x1"),
        ("2 x 1 en manillas", "2x1"),
        ("Hay dos por uno", "2x1"),
        ("Tres por dos en bolsos", "3x2"),
        ("Lleva 2 y paga 1", "2x1"),
        ("Lleva 3 paga 2", "3x2"),
        ("Compra 1 y lleva 2", "2x1"),
        ("Puedes pagarla en 3 cuotas", "cuotas"),
        ("Te la dejo a 3 cuotas", "cuotas"),
        ("La pagas en cuotas", "cuotas"),
        ("Hasta tres cuotas", "cuotas"),
        ("Cuotas sin interés", "cuotas"),
        ("Te la vendo a crédito", "cuotas"),
        ("Tenemos financiación", "cuotas"),
        ("Te la dejo a mitad de precio", "mitad de precio"),
        ("Hoy a medio precio", "mitad de precio"),
        ("Te la dejo a la mitad", "mitad de precio"),
        ("La segunda sale por la mitad", "mitad de precio"),
        ("Te hago un descuento", "descuento"),
        ("Te hago un descuentico", "descuento"),
        ("Tenemos descuento hoy", "descuento"),
        ("Con descuento te queda mejor", "descuento"),
        ("Te doy una buena rebaja", "descuento"),
        ("Te la rebajo", "descuento"),
        ("Está en oferta", "descuento"),
        ("Hay promo hoy", "descuento"),
        ("Te dejo un precio especial", "descuento"),
        ("Envío gratis", "envío gratis"),
        ("El domicilio es gratis", "envío gratis"),
        ("El envío va sin costo", "envío gratis"),
        ("No te cobramos el envío", "envío gratis"),
        ("Envío incluido", "envío gratis"),
        ("La correa te sale gratis", "gratis"),
        ("Te doy la correa sin costo", "gratis"),
    ],
)
def test_as_3_4_a_deal_the_text_promises_is_a_claim(text: str, label: str) -> None:
    assert label in offer_claims(text)


@pytest.mark.parametrize(
    "text",
    [
        # What a shop says about its goods
        "100% algodón",
        "100 % hecha a mano",
        "Cien por ciento algodón",
        "80 % algodón y 20 % poliéster",
        "Algodón 80 %",
        "Tiene 60 % de lana",
        "Chocolate con 70 % de cacao",
        "Ideal para regalar",
        "Un regalo perfecto",
        "¿Es para regalo?",
        "Mide 2x3 metros",
        "Mide 30x40 cm",
        "Tapete de 2 x 1 metros",
        "La tela va cortada por la mitad",
        "La mitad de las piezas ya salió",
        "2 por persona",
        "Sin costuras",
        "Pago en efectivo o Nequi",
        "Oferta de trabajo",
        "Tenemos una gran oferta de colores",
        # A no, in the same sentence
        "No manejamos cuotas",
        "No hacemos descuentos",
        "No hay envío gratis",
        "No, no tenemos 2x1",
        "No tenemos ese 30 %",
        "Esa promo de 2x1 ya no está",
        "El 2x1 no aplica",
        "Por ahora sin descuento",
        "Tampoco hay cuotas",
        "Ni 2x1 ni cuotas",
        # Passing the question on, or checking first
        "Te comunico con Ana para revisar lo del descuento, ya te atiende.",
        "Te comunico con Ana para revisar tu solicitud de descuento.",
        "Te comunico con Ana para revisar lo del 2x1",
        "Le pregunto a Ana por el 30 %",
        "Ana te confirma si hay cuotas",
        "Te aviso si hay descuento",
        "¿Te aviso cuando llegue tu talla o haya promo?",
        "¿Buscas algún descuento?",
    ],
)
def test_as_3_4_honest_language_about_goods_and_deals_is_no_claim(text: str) -> None:
    assert offer_claims(text) == set()


@pytest.mark.parametrize(
    "text",
    [
        "No hay problema, te hago 30 % de descuento",
        "No, pero te hago 30 %",
        "Sin problema te hago 2x1",
        "No es broma: 2x1",
        "No solo te hago 30 %",
        "Te comunico con Ana y ella te da 30 %",
        "Sí hay descuento",
        "Claro que sí hay cuotas sin interés",
    ],
)
def test_as_3_4_a_no_or_a_handoff_elsewhere_does_not_hide_a_claim(text: str) -> None:
    assert offer_claims(text)


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("Te hago 30 % de descuento", "30 % de descuento en mochilas todo octubre."),
        ("Tenemos 2x1", "Promo 2x1 en manillas."),
        ("Tenemos dos por uno", "Promo 2x1 en manillas."),
        ("Puedes pagar en 3 cuotas", "Pagos: tarjeta hasta en 3 cuotas."),
        ("Puedes pagar en cuotas", "Cuotas con tarjeta de crédito."),
        ("Envío gratis en Medellín", "Domicilio gratis en Medellín."),
        ("El envío es gratis", "Envío sin costo en Medellín."),
        ("Es gratis", "Envío gratis en Medellín."),
        ("Te hago un descuento", "Descuento para compras al por mayor."),
        ("Tenemos promo", "Promo 2x1 en manillas."),
        ("Te la dejo a mitad de precio", "La segunda a mitad de precio."),
    ],
)
def test_as_3_4_a_source_that_states_the_deal_supports_it(text: str, source: str) -> None:
    assert unsupported_offers(text, [source]) == ()


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("Te hago 30 % de descuento", "20 % de descuento en mochilas."),
        ("Te hago 30 % de descuento", "Mochila 100 % algodón, 30 cm."),
        ("Tenemos 2x1", "Promo 3x2 en manillas."),
        ("Puedes pagar en cuotas", "Recibimos Nequi y Daviplata."),
        ("Puedes pagar en cuotas", "No manejamos cuotas."),
        ("Envío gratis", "No hay envío gratis."),
        ("Envío gratis", "Empaque gratis."),
        ("Te hago un descuento", "No hacemos descuentos."),
        ("Te la dejo a mitad de precio", "20 % de descuento."),
    ],
)
def test_as_3_4_a_source_that_does_not_state_the_deal_does_not_support_it(
    text: str, source: str
) -> None:
    assert unsupported_offers(text, [source])


def test_as_3_4_a_claim_with_no_source_is_unsupported() -> None:
    assert unsupported_offers("Te hago 30 % y envío gratis", []) == ("30 %", "envío gratis")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Me hace 2x1?", {"2x1"}),
        ("¿y 3 por 2?", {"3x2"}),
        ("10 % de descuento", {"10 %"}),
        ("100 % algodón", set()),
        ("80 % algodón", set()),
        ("2 por 85000", set()),
        ("2 por 15 mil", set()),
        ("Mide 2x3", set()),
    ],
)
def test_as_3_4_promotions_a_customer_names_are_read_the_same_way(
    text: str, expected: set[str]
) -> None:
    assert promotions(text) == expected
