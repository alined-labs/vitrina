"""T024 agent loop v1 (FR-024, FR-025; plan.md §4.1): scope → retrieval → tool loop →
AgentTurnOutput → the checks outside the model, with one guided retry (AS-3.3, AS-3.4; R2–R5).
The scope classifier (T054) declines off-topic messages and hands sensitive ones to the owner
(R10, R11). It runs beside retrieval and the first agent call (NFR-01), and no tool runs and no
answer goes out before its verdict.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from vitrina_conversation.api import AgentDeps, Exchange, InMemoryBookings, TurnRequest, run_turn
from vitrina_conversation.contract import output_schema, tool_specs
from vitrina_conversation.engine import AdContext, booking_confirmation
from vitrina_conversation.scope import decline_reply, sensitive_reply
from vitrina_conversation.testing import ScriptedChat, Shop, final_answer, scope_answer, tool_call
from vitrina_conversation.tools import BookedSlot
from vitrina_core.providers import ChatRequest, ChatResponse, ProviderUnavailableError

FALLBACK = "No tengo ese dato, te comunico con Ana"
CONVERSATION = uuid4()


async def no_sleep(_seconds: float) -> None:
    return None


def deps(chat: Any, shop: Shop, **options: Any) -> AgentDeps:
    return AgentDeps(chat=chat, catalog=shop.catalog, model="spark-test", sleep=no_sleep, **options)


def request(shop: Shop, message: str, **options: Any) -> TurnRequest:
    return TurnRequest(seller=shop.seller, conversation_id=CONVERSATION, message=message, **options)


async def test_fr_024_the_model_gets_prompt_history_tools_and_schema(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("¡Hola, veci! ¿En qué te ayudo?", confidence=0.95)])
    history = (Exchange("Hola", "¡Hola! ¿En qué te ayudo?"),)

    await run_turn(
        request(shop, "¿Cuánto vale la mochila azul?", history=history), deps(chat, shop)
    )

    (sent,) = chat.requests
    assert (sent.model, sent.purpose, sent.tool_choice) == ("spark-test", "agent", "auto")
    assert [message.role for message in sent.messages] == ["system", "user", "assistant", "user"]
    assert sent.messages[-1].content == "¿Cuánto vale la mochila azul?"
    assert str(shop.ids["mochila-azul"]) in str(sent.messages[0].content)
    assert [tool.name for tool in sent.tools] == [spec.name for spec in tool_specs()]
    assert sent.response_format is not None
    assert sent.response_format.json_schema == output_schema()


async def test_as_3_3_a_tool_backed_cited_answer_reaches_the_customer(shop: Shop) -> None:
    reply = "El envío dentro de Medellín vale $8.000 y llega en 1 día hábil."
    chat = ScriptedChat(
        [
            tool_call("search_catalog", query="envío Medellín"),
            final_answer(reply, shop.ids["envio-medellin"]),
        ]
    )

    result = await run_turn(request(shop, "¿cuánto vale el envío a Medellín?"), deps(chat, shop))

    assert result.final_reply == reply
    assert result.cited_ids == (shop.ids["envio-medellin"],)
    assert result.review is not None
    assert result.review.price.passed
    assert result.review.confidence >= 0.6
    assert result.handoff is None
    (record,) = result.tool_calls
    assert (record.name, record.status) == ("search_catalog", "ok")
    tool_message = chat.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert str(shop.ids["envio-medellin"]) in str(tool_message.content)
    assert [call.purpose for call in result.model_calls] == ["classify", "agent", "agent"]


async def test_as_3_4_an_invented_price_gets_one_guided_retry(shop: Shop) -> None:
    fact = shop.ids["envio-medellin"]
    chat = ScriptedChat(
        [final_answer("El envío vale $12.000.", fact), final_answer("El envío vale $8.000.", fact)]
    )

    result = await run_turn(request(shop, "¿cuánto vale el envío?"), deps(chat, shop))

    assert result.final_reply == "El envío vale $8.000."
    assert result.review is not None
    assert result.review.retried
    retry = chat.requests[1].messages[-1]
    assert retry.role == "system"
    assert "$12.000" in str(retry.content)
    assert result.handoff is None


async def test_as_3_4_a_second_invented_price_falls_back_and_hands_off(shop: Shop) -> None:
    fact = shop.ids["envio-medellin"]
    chat = ScriptedChat([final_answer("Vale $12.000.", fact), final_answer("Vale $13.000.", fact)])

    result = await run_turn(request(shop, "¿cuánto vale el envío?"), deps(chat, shop))

    assert result.final_reply == FALLBACK
    assert result.handoff is not None
    assert result.handoff.reason == "price_unverified"
    assert result.cited_ids == ()


async def test_fr_024_tool_rounds_are_capped(shop: Shop) -> None:
    chat = ScriptedChat(
        [tool_call("search_catalog", query="mochila") for _ in range(4)]
        + [final_answer("¡Hola!", confidence=0.95)]
    )

    result = await run_turn(request(shop, "Hola"), deps(chat, shop))

    assert len(chat.requests) == 5
    assert chat.requests[-1].tool_choice == "none"
    assert len(result.tool_calls) == 4
    assert result.final_reply == "¡Hola!"


async def test_fr_025_fenced_json_is_read_without_a_repair_call(shop: Shop) -> None:
    chat = ScriptedChat(["```json\n" + final_answer("¡Hola!", confidence=0.95) + "\n```"])

    result = await run_turn(request(shop, "Hola"), deps(chat, shop))

    assert result.final_reply == "¡Hola!"
    assert len(chat.requests) == 1


async def test_fr_025_an_unreadable_answer_gets_one_repair_call(shop: Shop) -> None:
    reply = "La mochila azul vale $85.000."
    chat = ScriptedChat(
        ["Claro, la mochila azul vale $85.000", final_answer(reply, shop.ids["mochila-azul"])]
    )

    result = await run_turn(request(shop, "¿Cuánto vale?"), deps(chat, shop))

    assert result.final_reply == reply
    repair = chat.requests[1]
    assert (repair.messages[-1].role, repair.tool_choice) == ("system", "none")


async def test_fr_025_an_unrepairable_answer_falls_back_as_low_confidence(shop: Shop) -> None:
    chat = ScriptedChat(["no sé", "tampoco sé"])

    result = await run_turn(request(shop, "¿Cuánto vale?"), deps(chat, shop))

    assert (result.output, result.final_reply, result.handoff) == (None, FALLBACK, None)
    assert result.low_confidence_streak == 1


async def test_r4_the_streak_continues_from_the_conversation(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("Creo que sí.", shop.ids["mochila-azul"], confidence=0.3)])

    result = await run_turn(
        request(shop, "¿La tienen en verde?", low_confidence_streak=1), deps(chat, shop)
    )

    assert result.final_reply == FALLBACK
    assert result.handoff is not None
    assert result.handoff.reason == "low_confidence_twice"
    assert result.low_confidence_streak == 2


async def test_r5_a_handoff_reason_without_the_tool_opens_a_handoff(shop: Shop) -> None:
    chat = ScriptedChat(
        [final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")]
    )

    result = await run_turn(request(shop, "Quiero hablar con una persona"), deps(chat, shop))

    assert result.handoff is not None
    assert result.handoff.reason == "customer_request"
    assert "Quiero hablar con una persona" in result.handoff.summary


async def test_r5_a_handoff_tool_call_is_not_doubled(shop: Shop) -> None:
    chat = ScriptedChat(
        [
            tool_call(
                "handoff_to_human", reason="complaint_refund", summary="Quiere devolver la mochila."
            ),
            final_answer(
                "Lamento lo ocurrido, te comunico con Ana.", handoff_reason="complaint_refund"
            ),
        ]
    )

    result = await run_turn(request(shop, "La mochila llegó rota"), deps(chat, shop))

    assert result.handoff is not None
    assert result.handoff.summary == "Quiere devolver la mochila."


async def test_as_4_2_an_objection_tag_is_recorded_with_the_customers_words(shop: Shop) -> None:
    message = "Uy, $8.000 de envío está caro"
    chat = ScriptedChat(
        [
            final_answer(
                "Entiendo. El envío dentro de Medellín vale $8.000.",
                shop.ids["envio-medellin"],
                objection_tag="shipping",
            )
        ]
    )

    result = await run_turn(request(shop, message), deps(chat, shop))

    (objection,) = result.actions.objections
    assert (objection.tag, objection.quote) == ("shipping", message)


async def test_as_3_10_an_off_topic_question_gets_the_canned_decline(shop: Shop) -> None:
    chat = ScriptedChat(scope=[scope_answer(False, "deportes")])

    result = await run_turn(request(shop, "¿Quién ganó el partido?"), deps(chat, shop))

    assert (result.final_reply, result.off_topic) == (decline_reply(shop.seller), True)
    assert result.output is not None
    assert (result.output.objection_tag, result.output.cited_fact_ids) == ("off_topic", ())
    assert (result.retrieved, result.tool_calls, chat.requests) == ((), (), [])
    (objection,) = result.actions.objections
    assert (objection.tag, objection.quote) == ("off_topic", "¿Quién ganó el partido?")
    assert result.review is not None
    assert result.review.outcome == "accept"
    assert result.handoff is None
    assert [call.purpose for call in result.model_calls] == ["classify"]
    assert (result.scope.in_scope, result.scope.reason) == (False, "classifier: deportes")


async def test_as_3_10_the_agents_own_off_topic_tag_still_flags_a_decline(shop: Shop) -> None:
    reply = "Solo te puedo ayudar con Mochilas Ana. ¿Buscas alguna mochila?"
    chat = ScriptedChat([final_answer(reply, confidence=0.95, objection_tag="off_topic")])

    result = await run_turn(request(shop, "¿Quién ganó el partido?"), deps(chat, shop))

    assert result.off_topic
    assert result.final_reply == reply
    assert result.scope_check == (False, "off_topic: declined by the agent")


@pytest.mark.parametrize(
    ("sensitive", "reason"),
    [("medical", "medical_topic"), ("legal", "other"), ("financial", "other")],
)
async def test_r11_a_sensitive_question_goes_to_the_owner_unanswered(
    shop: Shop, sensitive: str, reason: str
) -> None:
    chat = ScriptedChat(scope=[scope_answer(True, "pide un consejo", sensitive)])
    message = "¿Esta mochila me sirve para el dolor de espalda?"

    result = await run_turn(request(shop, message), deps(chat, shop))

    assert result.final_reply == sensitive_reply(shop.seller)
    assert result.handoff is not None
    assert result.handoff.reason == reason
    assert message in result.handoff.summary
    assert (result.retrieved, chat.requests, result.off_topic) == ((), [], False)
    assert result.actions.objections == []


async def test_r10_a_classifier_outage_leaves_the_question_to_the_agent(shop: Shop) -> None:
    reply = "¡Hola! ¿En qué te ayudo?"
    chat = ScriptedChat(
        [final_answer(reply, confidence=0.95)],
        scope=[ProviderUnavailableError("down", provider="meta_model_api")],
    )

    result = await run_turn(request(shop, "¿Hacen domicilios?"), deps(chat, shop))

    assert (result.final_reply, len(chat.requests)) == (reply, 1)
    assert result.scope.in_scope


async def test_r10_a_greeting_needs_no_classifier_call(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("¡Hola! ¿En qué te ayudo?", confidence=0.95)])

    await run_turn(request(shop, "Hola, buenas tardes"), deps(chat, shop))

    assert (chat.classified, len(chat.requests)) == ([], 1)


async def test_art_viii_a_provider_hiccup_is_retried(shop: Shop) -> None:
    chat = ScriptedChat(
        [
            ProviderUnavailableError("down for a moment", provider="meta_model_api"),
            final_answer("¡Hola!", confidence=0.95),
        ]
    )

    result = await run_turn(request(shop, "Hola"), deps(chat, shop))

    assert result.final_reply == "¡Hola!"
    assert len(chat.requests) == 2


async def test_art_viii_a_turn_has_a_time_budget(shop: Shop) -> None:
    class SlowChat:
        async def complete(self, request: ChatRequest) -> ChatResponse:
            await asyncio.sleep(5)
            raise AssertionError("the budget should have stopped the turn")

    with pytest.raises(TimeoutError):
        await run_turn(request(shop, "Hola"), deps(SlowChat(), shop, budget_s=0.05))


async def test_r1_draft_items_never_reach_the_model(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("¡Hola!", confidence=0.95)])

    await run_turn(request(shop, "mochila edición especial"), deps(chat, shop))

    assert str(shop.ids["borrador"]) not in str(chat.requests[0].messages[0].content)


async def test_fr_024_only_the_last_turns_of_history_are_sent(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("¡Hola!", confidence=0.95)])
    history = tuple(Exchange(f"mensaje {n}", f"respuesta {n}") for n in range(5))

    await run_turn(request(shop, "Hola", history=history), deps(chat, shop, history_turns=2))

    sent = [message.content for message in chat.requests[0].messages[1:]]
    assert sent == ["mensaje 3", "respuesta 3", "mensaje 4", "respuesta 4", "Hola"]


async def test_r3_a_greeting_that_names_the_shop_needs_no_citation(shop: Shop) -> None:
    reply = "¡Hola! Bienvenida a Mochilas Ana. ¿En qué te ayudo?"
    chat = ScriptedChat([final_answer(reply, confidence=0.95)])

    result = await run_turn(request(shop, "Hola"), deps(chat, shop))

    assert result.final_reply == reply


async def test_r3_an_uncited_claim_never_reaches_the_customer(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("Sí, el envío es gratis a todo el país.", confidence=0.95)])

    result = await run_turn(request(shop, "¿El envío es gratis?"), deps(chat, shop))

    assert result.final_reply == FALLBACK
    assert result.low_confidence_streak == 1


async def test_r3_a_bare_yes_to_the_customers_question_never_reaches_them(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("Dale, listo.", confidence=0.95)])

    result = await run_turn(request(shop, "¿Tienen la mochila en verde?"), deps(chat, shop))

    assert result.final_reply == FALLBACK


TUESDAY_8AM = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
BOOK_SATURDAY = tool_call("book_slot", service="Arreglo", starts_at="2026-10-31T10:00:00-05:00")


async def test_t052_a_booking_the_review_rejects_is_confirmed_from_the_tool(shop: Shop) -> None:
    chat = ScriptedChat(
        [BOOK_SATURDAY, final_answer("Listo, te esperamos el sábado a las 10:00.", confidence=0.95)]
    )
    turn_deps = deps(chat, shop, bookings=InMemoryBookings(), clock=lambda: TUESDAY_8AM)

    result = await run_turn(
        request(shop, "¿Me agendas el sábado a las 10?", dry_run=False), turn_deps
    )

    assert result.final_reply == (
        "Listo, quedó tu cita de Arreglo para el sábado 31 de octubre de 2026, 10:00. "
        "¿Te ayudo con algo más?"
    )
    assert (result.handoff, result.low_confidence_streak) == (None, 1)


async def test_t052_a_booking_with_a_handoff_keeps_the_fallback(shop: Shop) -> None:
    invented = final_answer("Listo, son $50.000 por el arreglo.", confidence=0.95)
    chat = ScriptedChat([BOOK_SATURDAY, invented, invented])
    turn_deps = deps(chat, shop, bookings=InMemoryBookings(), clock=lambda: TUESDAY_8AM)

    result = await run_turn(request(shop, "Agéndame el sábado a las 10", dry_run=False), turn_deps)

    assert result.final_reply == FALLBACK
    assert result.handoff is not None
    assert result.handoff.reason == "price_unverified"


async def test_t052_a_reply_that_passes_after_a_booking_is_kept(shop: Shop) -> None:
    reply = "¡Con gusto, veci! ¿Te ayudo con algo más?"
    chat = ScriptedChat([BOOK_SATURDAY, final_answer(reply, confidence=0.95)])
    turn_deps = deps(chat, shop, bookings=InMemoryBookings(), clock=lambda: TUESDAY_8AM)

    result = await run_turn(
        request(shop, "¿Me agendas el sábado a las 10?", dry_run=False), turn_deps
    )

    assert result.final_reply == reply


def test_t052_two_bookings_are_confirmed_together(shop: Shop) -> None:
    saturday = datetime(2026, 10, 31, 10, 0, tzinfo=UTC) - timedelta(hours=-5)
    booked = [
        BookedSlot(uuid4(), "Corte", saturday),
        BookedSlot(uuid4(), "Barba", saturday + timedelta(minutes=30)),
    ]

    assert booking_confirmation(shop.seller, booked) == (
        "Listo, quedaron tus citas de Corte para el sábado 31 de octubre de 2026, 10:00 y "
        "Barba para el sábado 31 de octubre de 2026, 10:30. ¿Te ayudo con algo más?"
    )


async def test_t052_the_turns_clock_decides_what_has_passed(shop: Shop) -> None:
    saturday_8am = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)
    bookings = InMemoryBookings()
    chat = ScriptedChat(
        [
            tool_call("book_slot", service="Arreglo", starts_at="2026-10-24T10:00:00-05:00"),
            final_answer("¡Con gusto, veci! ¿Te ayudo con algo más?", confidence=0.95),
        ]
    )

    await run_turn(
        request(shop, "¿Me agendas hoy a las 10?", dry_run=False),
        deps(chat, shop, bookings=bookings, clock=lambda: saturday_8am),
    )

    assert len(bookings.bookings) == 1


class SlowClassifier(ScriptedChat):
    """The scope classifier answers only once the agent's first call has started (NFR-01)."""

    def __init__(self, *args: Any, agent_blocks: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.agent_started = asyncio.Event()
        self.agent_blocks = agent_blocks
        self.cancelled = False

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if request.purpose == "classify":
            await self.agent_started.wait()
            return await super().complete(request)
        self.agent_started.set()
        if self.agent_blocks:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return await super().complete(request)


async def test_nfr_01_the_agent_does_not_wait_for_the_scope_check(shop: Shop) -> None:
    reply = "El envío dentro de Medellín vale $8.000 y llega en 1 día hábil."
    chat = SlowClassifier([final_answer(reply, shop.ids["envio-medellin"])])

    result = await run_turn(
        request(shop, "¿cuánto vale el envío a Medellín?"), deps(chat, shop, budget_s=1)
    )

    assert result.final_reply == reply
    assert result.scope.in_scope
    assert [call.purpose for call in result.model_calls] == ["classify", "agent"]


async def test_r10_no_tool_runs_before_the_scope_verdict(shop: Shop) -> None:
    chat = SlowClassifier(
        [tool_call("reserve_item", product_id=str(shop.ids["mochila-azul"]), quantity=1)],
        scope=[scope_answer(False, "deportes")],
    )

    result = await run_turn(
        request(shop, "¿Quién ganó el partido?", dry_run=False), deps(chat, shop, budget_s=1)
    )

    assert result.final_reply == decline_reply(shop.seller)
    assert (result.tool_calls, result.actions.reservations) == ((), [])
    assert [call.purpose for call in result.model_calls] == ["classify", "agent"]


async def test_r11_a_sensitive_verdict_cancels_the_agent_call_in_flight(shop: Shop) -> None:
    chat = SlowClassifier(
        [final_answer("Sí, te sirve.", confidence=0.95)],
        scope=[scope_answer(True, "pide un consejo", "medical")],
        agent_blocks=True,
    )

    result = await run_turn(
        request(shop, "¿Esta mochila me sirve para el dolor de espalda?"),
        deps(chat, shop, budget_s=1),
    )

    assert result.final_reply == sensitive_reply(shop.seller)
    assert result.handoff is not None
    assert chat.cancelled
    assert [call.purpose for call in result.model_calls] == ["classify"]


async def test_nfr_01_each_call_carries_its_reasoning_effort(shop: Shop) -> None:
    chat = ScriptedChat(
        [tool_call("search_catalog", query="envío"), final_answer("¡Hola! ¿En qué te ayudo?")]
    )

    await run_turn(
        request(shop, "¿cuánto vale el envío a Medellín?"),
        deps(chat, shop, reasoning_effort="low", scope_reasoning_effort="minimal"),
    )

    assert [sent.reasoning_effort for sent in chat.requests] == ["low", "low"]
    assert [sent.reasoning_effort for sent in chat.classified] == ["minimal"]


async def test_nfr_01_a_sellers_calls_share_a_prompt_cache_key(shop: Shop) -> None:
    chat = ScriptedChat(
        [tool_call("search_catalog", query="envío"), final_answer("¡Hola! ¿En qué te ayudo?")]
    )

    await run_turn(request(shop, "¿cuánto vale el envío a Medellín?"), deps(chat, shop))

    seller = shop.seller.seller_id
    # Every customer of the seller shares the rules prefix; agent and scope prompts stay apart.
    assert [sent.prompt_cache_key for sent in chat.requests] == [f"agent:{seller}"] * 2
    assert [sent.prompt_cache_key for sent in chat.classified] == [f"classify:{seller}"]


async def test_as_3_2_the_agent_knows_which_product_the_ad_was_for(shop: Shop) -> None:
    product = shop.ids["mochila-azul"]
    chat = ScriptedChat([final_answer("¡Hola! ¿En qué te ayudo?", confidence=0.95)])

    await run_turn(
        request(shop, "Hola, vi el anuncio #A7", ad=AdContext("A7", product)), deps(chat, shop)
    )

    system = str(chat.requests[0].messages[0].content)
    assert f"anuncio #A7, de «Mochila azul» [{product}]" in system


async def test_as_3_2_an_ad_whose_product_is_gone_reads_as_an_unknown_code(shop: Shop) -> None:
    chat = ScriptedChat([final_answer("¡Hola! ¿En qué te ayudo?", confidence=0.95)])

    await run_turn(
        request(shop, "Hola, vi el anuncio #A7", ad=AdContext("A7", uuid4())), deps(chat, shop)
    )

    system = str(chat.requests[0].messages[0].content)
    assert "el código #A7, que no es de ningún anuncio vigente" in system


async def test_as_6_1_a_reply_ending_in_the_sellers_consent_question_reaches_them(
    shop: Shop,
) -> None:
    reply = f"Con gusto, quedo atenta. {shop.seller.consent_prompt_text}"
    chat = ScriptedChat([final_answer(reply, confidence=0.95, proposed_consent_prompt=True)])

    result = await run_turn(request(shop, "Gracias, lo pienso"), deps(chat, shop))

    assert result.final_reply == reply
