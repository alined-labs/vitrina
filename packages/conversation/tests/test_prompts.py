"""FR-024: the system prompt carries the seller, the brand voice, the retrieved items and the time."""

from datetime import UTC, datetime

from vitrina_catalog.api import SearchResult
from vitrina_catalog.sellers import BrandVoiceProfile
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


def test_fr_024_unregistered_hours_are_stated(shop: Shop) -> None:
    seller = shop.seller.model_copy(update={"business_hours": {}})

    assert "sin horario registrado" in render_system_prompt(seller, [], NOW)


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
