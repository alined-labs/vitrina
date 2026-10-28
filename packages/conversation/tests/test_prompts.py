"""FR-024: the system prompt carries the seller, the brand voice, the retrieved items and the time."""

from datetime import UTC, datetime

from vitrina_catalog.api import SearchResult
from vitrina_catalog.sellers import BrandVoiceProfile
from vitrina_conversation.engine import AdContext
from vitrina_conversation.prompts import render_system_prompt
from vitrina_conversation.testing import Shop

NOW = datetime(2026, 10, 27, 15, 30, tzinfo=UTC)  # 10:30 in Bogotá


def test_fr_024_prompt_names_the_business_and_the_owner(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert "de Mochilas Ana, el negocio de Ana" in prompt
    assert '"No tengo ese dato, te comunico con Ana"' in prompt


def test_fr_024_retrieved_items_are_listed_with_the_id_to_cite(shop: Shop) -> None:
    hit = SearchResult(
        id=shop.ids["mochila-azul"],
        kind="product",
        text="Mochila azul",
        price_cop=85000,
        stock=3,
        score=0.9,
    )

    prompt = render_system_prompt(shop.seller, [hit], NOW)

    (line,) = [line for line in prompt.splitlines() if str(hit.id) in line]
    assert "Mochila azul" in line
    assert "$85.000" in line
    assert "stock 3" in line


def test_fr_024_no_retrieved_items_is_stated(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert "(ningún dato recuperado para este mensaje)" in prompt


def test_fr_024_local_time_and_hours_are_in_spanish(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert "martes 27 de octubre de 2026, 10:30" in prompt
    assert "lunes 09:00–18:00; sábado 09:00–13:00" in prompt


def test_as_3_3_unconfigured_hours_point_to_the_catalog(shop: Shop) -> None:
    seller = shop.seller.model_copy(update={"business_hours": {}})

    prompt = render_system_prompt(seller, [], NOW)

    assert (
        "- Horario del negocio: no está en la configuración; si te preguntan, búscalo con "
        "search_catalog\n" in context(prompt)
    )
    assert "sin horario registrado" not in prompt


def test_fr_024_brand_voice_comes_from_the_profile(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert "Formalidad 2/5 · Calidez 4.5/5" in prompt
    assert "Regionalismos que el negocio sí usa: veci." in prompt


def test_fr_024_an_unmeasured_voice_gets_neutral_defaults(shop: Shop) -> None:
    seller = shop.seller.model_copy(update={"brand_voice_profile": BrandVoiceProfile()})

    prompt = render_system_prompt(seller, [], NOW)

    assert "Formalidad 3/5 · Calidez 3/5 · Emojis: 0 por mensaje" in prompt
    assert "Regionalismos que el negocio sí usa: ninguno." in prompt


def test_as_6_1_the_consent_question_is_the_sellers(shop: Shop) -> None:
    seller = shop.seller.model_copy(update={"consent_prompt_text": "¿Te aviso de las promos?"})

    assert '"¿Te aviso de las promos?"' in render_system_prompt(seller, [], NOW)


def test_as_3_10_off_topic_declines_end_with_a_question_back_to_the_shop(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert '"¿Te ayudo con algo de Mochilas Ana?"' in prompt


def test_as_3_8_the_order_threshold_is_in_the_context(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert "Monto que el negocio definió para pasar un pedido a Ana: más de $300.000" in prompt


def context(prompt: str) -> str:
    return prompt.split("CONTEXTO DE ESTA CONVERSACIÓN", 1)[1]


def test_as_3_2_the_prompt_names_the_ad_the_chat_came_from(shop: Shop) -> None:
    product = shop.ids["mochila-azul"]

    prompt = render_system_prompt(
        shop.seller, [], NOW, ad=AdContext("A7", product, "Mochila tejida azul")
    )

    assert (
        f"- El cliente llegó por el anuncio #A7, de «Mochila tejida azul» [{product}]: "
        "si pregunta por «el del anuncio», es ese producto." in context(prompt)
    )


def test_as_3_2_an_unknown_code_gets_a_greeting_and_a_question(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW, ad=AdContext("A9"))

    assert (
        "- El cliente mencionó el código #A9, que no es de ningún anuncio vigente: salúdalo y "
        "pregúntale qué producto vio, sin repetir el código ni afirmar nada sobre él. No es "
        "motivo para pasar la conversación a Ana." in context(prompt)
    )


def test_fr_024_a_chat_without_an_ad_code_gets_no_ad_line(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert "anuncio #" not in context(prompt)
    assert "código #" not in context(prompt)


def test_a_007_the_agent_cites_the_context_and_searches_only_what_it_lacks(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert (
        "1. Cada precio, disponibilidad, envío, pago, horario o política que afirmes debe venir de "
        "un dato con fact_id: de los datos recuperados en el contexto o, si no están ahí, de "
        "search_catalog." in prompt
    )
    assert "- search_catalog: para datos del negocio que no estén ya en el contexto." in prompt
    assert "llama a search_catalog y cita" not in prompt


def test_as_3_3_an_empty_search_gets_one_more_try(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert (
        "Si no trae nada, búscalo una vez más con una o dos palabras clave, como el nombre del "
        "producto o el tema, antes de decir que no tienes el dato." in prompt
    )


def test_as_3_4_totals_and_other_currencies_get_the_cited_prices(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert (
        "Si te piden un total, no escribas la suma: da el precio de cada producto con su dato "
        "citado, y pasa la conversación solo si el pedido supera el monto que el negocio definió."
        in prompt
    )
    assert (
        "Los precios están en pesos colombianos: si te los piden en otra moneda, da el precio en "
        "pesos, sin convertirlo." in prompt
    )


def test_as_3_10_a_shop_question_beside_an_off_topic_one_is_answered(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert (
        "Si el mismo mensaje también pregunta algo del negocio, declina la parte ajena en una "
        "frase, responde la del negocio con su dato citado y no marques off_topic." in prompt
    )


def test_as_3_8_an_exchange_question_gets_the_policy_before_any_handoff(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert (
        "Si preguntan si pueden cambiar un producto, primero explica la política de cambios con su "
        "dato citado: pasa la conversación solo si el cliente reclama o insiste en una excepción."
        in prompt
    )


def test_as_3_8_each_handoff_trigger_names_its_reason(shop: Shop) -> None:
    prompt = render_system_prompt(shop.seller, [], NOW)

    assert (
        "6. Llama a handoff_to_human cuando: el cliente pide hablar con una persona (reason "
        "customer_request); reclama por un producto o un pedido, o pide una devolución o un "
        "reembolso (complaint_refund); hay un problema de pago (payment_issue); el tema es médico "
        "(medical_topic); el cliente está molesto o se queja de la atención sin reclamar por un "
        "producto (negative_sentiment); el pedido supera el monto que el negocio definió "
        "(amount_over_threshold); piden un descuento o una excepción fuera de la política "
        "(policy_exception); o no tienes el dato." in prompt
    )

