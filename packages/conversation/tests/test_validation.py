"""AS-3.3, AS-3.4, FR-026: the checks that run outside the model on every answer (R2–R5).

The amounts come from ``vitrina_core.money``, the same normalizer that filled the catalog's
``amounts_cop``, so a reply is judged with the exact rules its citations were stored with.
"""

from uuid import UUID, uuid4

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from vitrina_catalog.api import Citation
from vitrina_conversation.contract import AgentTurnOutput, HandoffReason
from vitrina_conversation.validation import (
    FALLBACK_REPLY,
    check_prices,
    retry_instruction,
    review,
)
from vitrina_core.money import amounts_cop, format_cop

OWNER = "Ana"
FALLBACK = "No tengo ese dato, te comunico con Ana"
SHIPPING = Citation(id=uuid4(), kind="shipping", amounts_cop=(12000,))
BAG = Citation(id=uuid4(), kind="product", amounts_cop=(85000,))


def answer(
    reply: str,
    *cited: UUID,
    confidence: float = 0.9,
    handoff_reason: HandoffReason | None = None,
) -> AgentTurnOutput:
    return AgentTurnOutput(
        reply=reply,
        cited_fact_ids=cited,
        confidence=confidence,
        handoff_reason=handoff_reason,
        objection_tag=None,
        proposed_consent_prompt=False,
    )


def test_as_3_3_a_cited_price_passes() -> None:
    output = answer("El envío a Medellín vale $12.000 y llega en 2 a 3 días.", SHIPPING.id)

    result = review(output, [SHIPPING], owner_name=OWNER)

    assert (result.outcome, result.final_reply, result.handoff_reason) == (
        "accept",
        output.reply,
        None,
    )
    assert result.price.passed
    assert (result.price.amounts_in_reply, result.price.amounts_in_citations) == (
        (12000,),
        (12000,),
    )
    assert result.confidence >= 0.6


def test_as_3_4_an_uncited_amount_asks_for_one_guided_retry() -> None:
    output = answer("El envío a Medellín vale $15.000.", SHIPPING.id)

    result = review(output, [SHIPPING], owner_name=OWNER)

    assert (result.outcome, result.final_reply) == ("retry", None)
    assert result.price.violations == (15000,)
    assert "$15.000" in retry_instruction(result)


def test_as_3_4_a_second_violation_falls_back_and_hands_off() -> None:
    output = answer("El envío a Medellín vale $15.000.", SHIPPING.id)

    result = review(output, [SHIPPING], owner_name=OWNER, retried=True)

    assert (result.outcome, result.final_reply, result.handoff_reason) == (
        "fallback",
        FALLBACK,
        "price_unverified",
    )
    assert result.retried


@pytest.mark.parametrize(
    ("reply", "violations"),
    [
        ("Te sale en doce mil pesos.", ()),
        ("Son 12 mil.", ()),
        ("Te sale en quince mil pesos.", (15000,)),
        ("Son 15 lucas.", (15000,)),
        ("Te queda en $15k.", (15000,)),
        ("Vale 12.000 el envío y la mochila 85.000.", (85000,)),
    ],
)
def test_as_3_4_every_way_of_writing_an_amount_is_checked(
    reply: str, violations: tuple[int, ...]
) -> None:
    assert check_prices(reply, [SHIPPING]).violations == violations


def test_fr_026_an_amount_without_any_citation_is_a_violation() -> None:
    result = review(answer("La mochila vale $85.000."), [], owner_name=OWNER)

    assert (result.outcome, result.price.violations) == ("retry", (85000,))


def test_fr_026_amounts_are_checked_against_the_union_of_citations() -> None:
    reply = "La mochila vale $85.000 y el envío $12.000."

    assert check_prices(reply, [SHIPPING, BAG]).passed
    assert not check_prices(reply, [BAG]).passed


def test_r3_citing_an_id_the_catalog_does_not_know_counts_as_confidence_zero() -> None:
    stranger = uuid4()

    result = review(answer("Sí, la tenemos en rojo.", stranger), [BAG], owner_name=OWNER)

    assert not result.groundedness.passed
    assert str(stranger) in result.groundedness.uncited_claims[0]
    assert (result.confidence, result.outcome, result.final_reply) == (0.0, "fallback", FALLBACK)


def test_r3_a_greeting_needs_no_citation() -> None:
    output = answer("¡Hola, veci! ¿En qué te puedo ayudar?", confidence=0.95)

    result = review(output, [], owner_name=OWNER)

    assert (result.outcome, result.final_reply) == ("accept", output.reply)
    assert result.groundedness.passed


def test_r4_low_confidence_falls_back_and_counts_without_handing_off() -> None:
    result = review(answer("Creo que sí hay.", BAG.id, confidence=0.4), [BAG], owner_name=OWNER)

    assert (result.outcome, result.final_reply, result.handoff_reason) == (
        "fallback",
        FALLBACK,
        None,
    )
    assert result.low_confidence_streak == 1


def test_r4_a_second_low_confidence_turn_hands_off() -> None:
    output = answer("Creo que sí hay.", BAG.id, confidence=0.4)

    result = review(output, [BAG], owner_name=OWNER, low_confidence_streak=1)

    assert (result.outcome, result.handoff_reason) == ("fallback", "low_confidence_twice")
    assert result.low_confidence_streak == 2


def test_r4_a_confident_turn_resets_the_streak() -> None:
    output = answer("La mochila vale $85.000.", BAG.id)

    result = review(output, [BAG], owner_name=OWNER, low_confidence_streak=1)

    assert (result.outcome, result.low_confidence_streak) == ("accept", 0)


def test_r4_the_threshold_is_the_sellers() -> None:
    output = answer("La mochila vale $85.000.", BAG.id, confidence=0.7)

    result = review(output, [BAG], owner_name=OWNER, low_confidence_threshold=0.8)

    assert result.outcome == "fallback"


def test_r5_a_handoff_the_model_chose_keeps_its_reason_and_reply() -> None:
    output = answer(
        "Lamento lo ocurrido, te comunico con Ana.",
        confidence=0.3,
        handoff_reason="complaint_refund",
    )

    result = review(output, [], owner_name=OWNER, low_confidence_streak=1)

    assert (result.outcome, result.final_reply, result.handoff_reason) == (
        "accept",
        output.reply,
        "complaint_refund",
    )
    assert result.low_confidence_streak == 0


def test_r2_also_guards_a_handoff_reply() -> None:
    output = answer("Ana te confirma el envío de $20.000.", handoff_reason="customer_request")

    assert review(output, [SHIPPING], owner_name=OWNER).outcome == "retry"


def test_fallback_names_the_owner() -> None:
    assert FALLBACK_REPLY.format(owner_name="Luis") == "No tengo ese dato, te comunico con Luis"


# ── properties (Hypothesis): R2 and data-model invariant 7 ──

AMOUNT = st.integers(min_value=1, max_value=9_999_999)


@given(st.lists(AMOUNT, min_size=1, max_size=4, unique=True), st.data())
def test_fr_026_cited_amounts_always_pass(cited: list[int], data: st.DataObject) -> None:
    citation = Citation(id=uuid4(), kind="product", amounts_cop=tuple(cited))
    quoted = data.draw(st.lists(st.sampled_from(cited), min_size=1, max_size=3))

    reply = "Te queda en " + " o ".join(format_cop(amount) for amount in quoted) + "."

    assert check_prices(reply, [citation]).passed


@given(st.sets(AMOUNT, max_size=3), AMOUNT)
def test_fr_026_an_invented_amount_never_passes(cited: set[int], invented: int) -> None:
    assume(invented not in cited)
    citation = Citation(id=uuid4(), kind="product", amounts_cop=tuple(cited))

    check = check_prices(f"El precio es {format_cop(invented)}.", [citation])

    assert not check.passed
    assert invented in check.violations


@given(
    st.lists(AMOUNT, max_size=3),
    st.sets(AMOUNT, max_size=3),
    st.floats(min_value=0, max_value=1),
    st.booleans(),
    st.integers(min_value=0, max_value=3),
)
def test_fr_026_an_accepted_reply_only_quotes_cited_amounts(
    quoted: list[int], cited: set[int], confidence: float, retried: bool, streak: int
) -> None:
    citation = Citation(id=uuid4(), kind="product", amounts_cop=tuple(cited))
    reply = "Precios: " + ", ".join(format_cop(amount) for amount in quoted) if quoted else "Hola"
    output = answer(reply, citation.id, confidence=confidence)

    result = review(
        output, [citation], owner_name=OWNER, retried=retried, low_confidence_streak=streak
    )

    if result.outcome == "accept":
        assert result.final_reply is not None
        assert set(amounts_cop(result.final_reply)) <= cited


def test_as_3_3_confidence_exactly_at_the_threshold_passes() -> None:
    output = answer("La mochila vale $85.000.", BAG.id, confidence=0.6)

    assert review(output, [BAG], owner_name=OWNER).outcome == "accept"


# ── R3: a reply without citations may greet, thank or ask, never state (MX1 review) ──


@pytest.mark.parametrize(
    "reply",
    [
        "Sí, el envío es gratis a todo el país.",
        "Llega mañana mismo a Bello.",
        "Claro, tenemos la talla M en azul.",
        "Tenemos la talla M en azul, ¿te la reservo?",
        "Te la dejo en 3 días.",
        "¿Te llega en 2 días?",
    ],
)
def test_r3_an_uncited_claim_counts_as_confidence_zero(reply: str) -> None:
    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER)

    assert (result.outcome, result.final_reply, result.confidence) == ("fallback", FALLBACK, 0.0)
    assert not result.groundedness.passed


@pytest.mark.parametrize(
    "reply",
    [
        "¡Hola, veci! ¿En qué te puedo ayudar?",
        "Claro, ¿qué talla buscas?",
        "¡Mil gracias, veci! 🙌",
        "Con mucho gusto.",
        "¡Hola! Bienvenida a Mochilas Ana. ¿En qué te ayudo?",
        "En qué te ayudo?",
    ],
)
def test_r3_greetings_thanks_and_questions_need_no_citation(reply: str) -> None:
    result = review(
        answer(reply, confidence=0.9), [], owner_name=OWNER, business_name="Mochilas Ana"
    )

    assert (result.outcome, result.final_reply) == ("accept", reply)
    assert result.groundedness.passed


def test_r3_an_off_topic_decline_is_not_a_claim_about_the_shop() -> None:
    output = AgentTurnOutput(
        reply="Solo te puedo ayudar con Mochilas Ana, veci.",
        cited_fact_ids=(),
        confidence=0.9,
        handoff_reason=None,
        objection_tag="off_topic",
        proposed_consent_prompt=False,
    )

    assert review(output, [], owner_name=OWNER).outcome == "accept"


def test_r3_an_off_topic_reply_with_an_amount_is_still_a_claim() -> None:
    output = AgentTurnOutput(
        reply="No sé del partido, pero la mochila vale $85.000.",
        cited_fact_ids=(),
        confidence=0.9,
        handoff_reason=None,
        objection_tag="off_topic",
        proposed_consent_prompt=False,
    )

    assert review(output, [], owner_name=OWNER).outcome != "accept"


def test_r3_the_fallback_sentence_is_not_a_claim() -> None:
    result = review(answer(FALLBACK, confidence=0.9), [], owner_name=OWNER)

    assert result.groundedness.passed


def test_r3_one_unknown_id_among_valid_ones_still_counts_as_confidence_zero() -> None:
    output = answer("La mochila vale $85.000.", BAG.id, uuid4())

    result = review(output, [BAG], owner_name=OWNER)

    assert (result.confidence, result.outcome) == (0.0, "fallback")
    assert len(result.groundedness.uncited_claims) == 1


# ── R3 follow-up (MX1 review of #51): claims inside questions, a bare yes, "te saluda" ──


@pytest.mark.parametrize(
    "reply",
    [
        "¿Te lo envío gratis mañana?",
        "¿Sabías que el envío es gratis?",
        "¿Quieres que te haga un descuento?",
        "¿Hay en rojo, te la separo?",
    ],
)
def test_r3_a_claim_inside_a_question_is_still_a_claim(reply: str) -> None:
    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER)

    assert (result.outcome, result.final_reply) == ("fallback", FALLBACK)


@pytest.mark.parametrize(
    ("customer", "reply"),
    [
        ("¿me lo dejan en 70 mil?", "¡Claro que sí, veci!"),
        ("¿Tienen la mochila en verde?", "Dale, listo."),
        ("¿Hacen envíos a Pasto?", "No, veci."),
        ("me lo dejan en 70 mil", "¡Claro que sí!"),
        ("tienen en verde", "Sí, veci."),
    ],
)
def test_r3_a_bare_yes_or_no_to_a_question_is_a_claim(customer: str, reply: str) -> None:
    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER, customer_message=customer)

    assert (result.outcome, result.final_reply) == ("fallback", FALLBACK)


def test_r3_courtesy_words_after_thanks_are_not_an_answer() -> None:
    result = review(
        answer("¡Listo, veci! Con gusto.", confidence=0.9),
        [],
        owner_name=OWNER,
        customer_message="Muchas gracias",
    )

    assert result.outcome == "accept"


@pytest.mark.parametrize(
    "reply",
    [
        "Con gusto, Tejidos Vecina te saluda. ¿Qué talla buscas?",
        "¿En qué te puedo ayudar?",
        "¿Qué color prefieres?",
        "¿Para qué ciudad sería el envío?",
    ],
)
def test_r3_greetings_and_neutral_questions_still_pass(reply: str) -> None:
    result = review(
        answer(reply, confidence=0.9),
        [],
        owner_name=OWNER,
        business_name="Tejidos Vecina",
        customer_message="Hola",
    )

    assert (result.outcome, result.final_reply) == ("accept", reply)


# ── R3: everyday greetings and closings are not claims (live replay, 2026-10-28) ──


@pytest.mark.parametrize(
    "reply",
    [
        "Con gusto, quedo atenta si te decides o necesitas algo más.",
        "Con gusto, quedo atenta si te decides o necesitas algo mas.",
        "Hola, gracias por escribir a Mochilas Ana.",
        "Gracias por escribirnos. Aquí estamos para lo que necesites.",
        "Cuando quieras, aquí seguimos.",
        "Quedo pendiente por si tienes alguna duda.",
        "Con gusto, que tengas un buen día.",
    ],
)
def test_r3_closings_and_thanks_need_no_citation(reply: str) -> None:
    result = review(
        answer(reply, confidence=0.9), [], owner_name=OWNER, business_name="Mochilas Ana"
    )

    assert (result.outcome, result.final_reply) == ("accept", reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Si necesitas algo más, el envío es gratis.",
        "Quedo atenta, ya no tenemos la talla M.",
        "Cuando quieras pasas, abrimos el domingo.",
        "El anuncio no me aparece como vigente.",
    ],
)
def test_r3_a_closing_that_states_a_fact_is_still_a_claim(reply: str) -> None:
    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER)

    assert (result.outcome, result.final_reply) == ("fallback", FALLBACK)


CONSENT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"


def test_r3_the_sellers_own_consent_question_is_not_a_claim() -> None:
    reply = f"Perfecto, quedo atenta si te decides. {CONSENT}"

    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER, consent_prompt=CONSENT)

    assert (result.outcome, result.final_reply) == ("accept", reply)


def test_r3_a_promo_question_of_the_models_own_is_still_a_claim() -> None:
    reply = "¿Te aviso cuando haya promo de mochilas?"

    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER, consent_prompt=CONSENT)

    assert (result.outcome, result.final_reply) == ("fallback", FALLBACK)


@pytest.mark.parametrize(
    "reply",
    [
        "Cuéntame qué producto viste para ayudarte con disponibilidad y precio.",
        "Dime qué color prefieres.",
        "Escríbeme tu ciudad y te ayudo.",
        "¡Hola! Cuéntame qué buscas.",
        "Hola, bienvenido a Mochilas Ana. ¿Qué producto viste en el anuncio? Cuéntame para ayudarte.",
        "Hola, gracias por escribir a Mochilas Ana. Cuéntame qué producto viste para ayudarte con la información.",
    ],
)
def test_r3_a_request_to_the_customer_reads_like_a_question(reply: str) -> None:
    result = review(
        answer(reply, confidence=0.9), [], owner_name=OWNER, business_name="Mochilas Ana"
    )

    assert (result.outcome, result.final_reply) == ("accept", reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Cuéntame si quieres el envío gratis.",
        "Dime y te la dejo con descuento.",
        "¿Viste el anuncio de la promo?",
    ],
)
def test_r3_a_request_that_states_a_fact_is_still_a_claim(reply: str) -> None:
    result = review(answer(reply, confidence=0.9), [], owner_name=OWNER)

    assert (result.outcome, result.final_reply) == ("fallback", FALLBACK)
