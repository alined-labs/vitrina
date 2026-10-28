"""T054 scope classifier (R10, R11; FR-029, AS-3.10): is this message about the shop?

Courtesy and follow-ups are in scope by rule, without a model call. Everything else is one
short Spark call (purpose ``classify``) that answers JSON. A failed call or an unreadable
answer fails open: in scope, nothing sensitive, and the agent's own rules apply.
"""

import json

import pytest

from vitrina_conversation.scope import (
    SCOPE_SCHEMA,
    ScopeVerdict,
    classify_scope,
    decline_reply,
    in_scope_by_rule,
)
from vitrina_conversation.testing import ScriptedChat, Shop, scope_answer
from vitrina_core.providers import ProviderError


@pytest.mark.parametrize(
    "message",
    [
        "Hola",
        "hola!!",
        "Buenas tardes",
        "Muchas gracias 🙏",
        "ok",
        "Sí, dale",
        "?",
        "",
        "👍",
        "Chao, gracias",
        "Bueno, hasta luego",
        "Hola, vi el anuncio #A7",
        "#a7",
        "Por favor avísame",
        "No, gracias",
    ],
)
def test_r10_courtesy_and_follow_ups_are_in_scope_by_rule(message: str) -> None:
    assert in_scope_by_rule(message)


@pytest.mark.parametrize(
    "message",
    [
        "¿Quién ganó el partido?",
        "¿Cuánto vale la mochila azul?",
        "Hola, ¿tienen mochilas?",
        "¿Me ayudas con la tarea de matemáticas?",
        "vi el #A7, ¿quién ganó el partido?",
    ],
)
def test_r10_anything_else_goes_to_the_classifier(message: str) -> None:
    assert not in_scope_by_rule(message)


async def test_r10_a_courtesy_message_costs_no_model_call(shop: Shop) -> None:
    chat = ScriptedChat()

    verdict = await classify_scope("¡Gracias, veci!", shop.seller, chat=chat, model="spark-test")

    assert (verdict.in_scope, verdict.sensitive, verdict.call) == (True, "none", None)
    assert chat.classified == []


async def test_as_3_10_the_classifier_finds_a_question_off_topic(shop: Shop) -> None:
    chat = ScriptedChat(scope=[scope_answer(False, " deportes,\n no es del negocio ")])

    verdict = await classify_scope(
        "¿Quién ganó el partido?", shop.seller, chat=chat, model="spark-test"
    )

    assert (verdict.in_scope, verdict.sensitive) == (False, "none")
    assert verdict.reason == "classifier: deportes, no es del negocio"
    assert verdict.call is not None
    (sent,) = chat.classified
    assert (sent.model, sent.purpose, sent.temperature, sent.tools) == (
        "spark-test",
        "classify",
        0.0,
        (),
    )
    assert [message.role for message in sent.messages] == ["system", "user"]
    assert "Mochilas Ana" in str(sent.messages[0].content)
    assert "productos" in str(sent.messages[0].content)
    assert sent.messages[1].content == "¿Quién ganó el partido?"
    assert sent.response_format is not None
    assert sent.response_format.json_schema == SCOPE_SCHEMA


@pytest.mark.parametrize("sensitive", ["medical", "legal", "financial"])
async def test_r11_the_classifier_flags_sensitive_questions(shop: Shop, sensitive: str) -> None:
    chat = ScriptedChat(scope=[scope_answer(True, "pide un consejo", sensitive)])

    verdict = await classify_scope(
        "¿Esta crema me sirve para la dermatitis?", shop.seller, chat=chat, model="spark-test"
    )

    assert (verdict.in_scope, verdict.sensitive) == (True, sensitive)


@pytest.mark.parametrize(
    "answer",
    [
        "no sé",
        '{"in_scope": "tal vez"}',
        json.dumps({"in_scope": False, "reason": "x", "sensitive": "religious"}),
        ProviderError("down", provider="meta_model_api"),
        TimeoutError(),
    ],
    ids=["prose", "wrong-type", "unknown-sensitive", "provider-error", "timeout"],
)
async def test_r10_the_classifier_fails_open(shop: Shop, answer: str | Exception) -> None:
    chat = ScriptedChat(scope=[answer])

    verdict = await classify_scope("¿Hacen domicilios?", shop.seller, chat=chat, model="spark-test")

    assert (verdict.in_scope, verdict.sensitive) == (True, "none")
    assert verdict.reason.startswith("in scope by default")


async def test_r10_a_fenced_answer_is_read_and_its_reason_kept_short(shop: Shop) -> None:
    long_reason = "habla de fútbol " * 10
    fenced = f"```json\n{scope_answer(False, long_reason)}\n```"
    chat = ScriptedChat(scope=[fenced])

    verdict = await classify_scope("¿Y el partido?", shop.seller, chat=chat, model="spark-test")

    assert not verdict.in_scope
    assert len(verdict.reason) <= len("classifier: ") + 80


@pytest.mark.parametrize("strict", [True, False])
async def test_r10_the_classifier_asks_for_the_schema_as_configured(
    shop: Shop, strict: bool
) -> None:
    chat = ScriptedChat()

    await classify_scope(
        "¿Hacen domicilios?", shop.seller, chat=chat, model="spark-test", strict_schema=strict
    )

    (sent,) = chat.classified
    assert sent.response_format is not None
    assert sent.response_format.strict is strict


def test_as_3_10_the_decline_names_the_shop_and_asks_back(shop: Shop) -> None:
    products = decline_reply(shop.seller)
    services = decline_reply(shop.seller.model_copy(update={"category": "services"}))

    assert "Mochilas Ana" in products
    assert products.endswith("?")
    assert "catálogo" in products
    assert "citas" in services


def test_r10_the_default_verdict_is_in_scope() -> None:
    assert ScopeVerdict(in_scope=True).sensitive == "none"
