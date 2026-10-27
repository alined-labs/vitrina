"""T040 copy grid: planned cells, one Spark call, checks outside the model, one rewrite (AS-2.4)."""

import json
import re
from collections.abc import Callable

import pytest

from vitrina_core.providers import CallMeta, ChatMessage, ChatRequest, ChatResponse
from vitrina_creative.api import (
    BrandVoice,
    CopyError,
    CopyInput,
    plan_grid,
    write_copy,
)
from vitrina_creative.copy import ANGLES, MAX_CHARS, WA_LINK, check_copy

BAG = CopyInput(
    business_name="Tejidos Vecina",
    city="Medellín",
    product_name="Bolso tejido a mano",
    product_description="Bolso en hilo de algodón, tejido a mano.",
    price_cop=85_000,
    stock=3,
    facts=("Envío a Medellín $12.000, 2–3 días",),
    voice=BrandVoice(formality=2, warmth=5, emoji_rate=0.5, regionalisms=("veci",)),
)
SLOT_LINE = re.compile(r"^(\d+)\. hook=(\w+), angle=(\w+)", re.MULTILINE)


def good_text(slot: int) -> str:
    return f"Veci, el bolso {slot} tejido a mano vale $85.000. Escríbenos: {WA_LINK}"


class ScriptedWriter:
    """Answers each request through ``reply(slots, attempt)``, where slots are 1-based."""

    def __init__(self, reply: Callable[[list[int], int], dict[int, str] | str]) -> None:
        self.reply = reply
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        user = request.messages[1].content
        assert isinstance(user, str)
        slots = [int(match.group(1)) for match in SLOT_LINE.finditer(user)]
        answer = self.reply(slots, len(self.requests))
        text = (
            answer
            if isinstance(answer, str)
            else json.dumps(
                {"variants": [{"slot": s, "text": t} for s, t in answer.items()]},
                ensure_ascii=False,
            )
        )
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return ChatResponse(message=ChatMessage(role="assistant", content=text), meta=meta)


def all_good(slots: list[int], attempt: int) -> dict[int, str]:
    return {slot: good_text(slot) for slot in slots}


def test_as_2_4_the_grid_has_the_requested_count_and_every_angle() -> None:
    cells = plan_grid(10)
    assert len(cells) == 10
    assert {angle for _, angle in cells} == set(ANGLES)
    assert len(set(cells)) == 10


def test_cells_repeat_only_after_every_pair_is_used() -> None:
    assert len(set(plan_grid(36))) == 36
    assert len(set(plan_grid(37))) == 36


def test_requested_angles_limit_the_grid() -> None:
    cells = plan_grid(4, ["price", "gift"])
    assert {angle for _, angle in cells} == {"price", "gift"}
    assert len(set(cells)) == 4


async def test_scarcity_needs_a_low_known_stock() -> None:
    for stock, expected in ((3, True), (None, False), (40, False)):
        writer = ScriptedWriter(all_good)
        item = CopyInput(**{**BAG.__dict__, "stock": stock})
        result = await write_copy(item, writer=writer, model="m")
        assert any(v.hook == "scarcity" for v in result.variants) is expected, stock


async def test_the_handmade_angle_needs_the_product_data_to_say_so() -> None:
    plain = CopyInput(
        **{**BAG.__dict__, "product_name": "Camiseta básica", "product_description": "Algodón."}
    )
    result = await write_copy(plain, writer=ScriptedWriter(all_good), model="m")
    assert all(v.angle != "handmade" for v in result.variants)
    result = await write_copy(BAG, writer=ScriptedWriter(all_good), model="m")
    assert any(v.angle == "handmade" for v in result.variants)


async def test_as_2_4_returns_the_requested_texts_on_their_planned_cells() -> None:
    writer = ScriptedWriter(all_good)
    result = await write_copy(BAG, count=10, writer=writer, model="copy-model")
    assert [(v.hook, v.angle) for v in result.variants] == plan_grid(10)
    assert all(len(v.text) <= MAX_CHARS and v.text.count(WA_LINK) == 1 for v in result.variants)
    assert result.dropped == ()
    assert len(writer.requests) == 1


async def test_the_request_is_one_spark_copy_call_with_an_exact_count_schema() -> None:
    writer = ScriptedWriter(all_good)
    await write_copy(BAG, writer=writer, model="copy-model")
    (request,) = writer.requests
    assert (request.model, request.purpose) == ("copy-model", "copy")
    assert request.response_format is not None
    items = request.response_format.json_schema["properties"]["variants"]
    assert (items["minItems"], items["maxItems"]) == (10, 10)
    system, user = request.messages[0].content, request.messages[1].content
    assert isinstance(system, str)
    assert isinstance(user, str)
    assert "$85.000" in system
    assert "Envío a Medellín $12.000" in user
    assert "veci" in user
    assert len(SLOT_LINE.findall(user)) == 10


async def test_failing_cells_are_rewritten_once_with_their_problems() -> None:
    def reply(slots: list[int], attempt: int) -> dict[int, str]:
        texts = {slot: good_text(slot) for slot in slots}
        if attempt == 1:
            texts[3] = "x" * (MAX_CHARS + 1) + WA_LINK
            texts[5] = "Bolso tejido a mano por $85.000, escríbenos ya."
            texts[7] = f"Antes $90.000, hoy $85.000. {WA_LINK}"
        return texts

    writer = ScriptedWriter(reply)
    result = await write_copy(BAG, writer=writer, model="m")
    assert len(result.variants) == 10
    assert result.dropped == ()
    second = writer.requests[1].messages[1].content
    assert isinstance(second, str)
    assert [int(n) for n, _, _ in SLOT_LINE.findall(second)] == [3, 5, 7]
    assert "caracteres" in second
    assert WA_LINK in second
    assert "$90.000" in second


async def test_cells_still_failing_after_the_rewrite_are_dropped_and_reported() -> None:
    def reply(slots: list[int], attempt: int) -> dict[int, str]:
        texts = {slot: good_text(slot) for slot in slots}
        texts[4] = "Nuestros clientes dicen que es el mejor bolso. " + WA_LINK
        return texts

    result = await write_copy(BAG, writer=ScriptedWriter(reply), model="m")
    assert len(result.variants) == 9
    (dropped,) = result.dropped
    assert (dropped.hook, dropped.angle) == plan_grid(10)[3]
    assert "testimonio" in dropped.problems[0]


async def test_no_usable_copy_fails_the_job() -> None:
    with pytest.raises(CopyError) as caught:
        await write_copy(BAG, writer=ScriptedWriter(lambda slots, attempt: "no sé"), model="m")
    assert caught.value.code == "COPY_FAILED"


def test_a_fact_amount_is_allowed_but_an_invented_one_is_not() -> None:
    allowed = {85_000, 12_000}
    assert check_copy(f"Bolso a $85.000 y envío a Medellín $12.000. {WA_LINK}", allowed) == []
    (problem,) = check_copy(f"Bolso a $85.000, hoy $79.900. {WA_LINK}", allowed)
    assert "$79.900" in problem


@pytest.mark.parametrize(
    "text",
    [
        "Nuestros clientes dicen que es el mejor.",
        "⭐⭐⭐⭐⭐ el favorito.",
        "El más vendido de Medellín.",
        '"Me encantó" — Ana',
        "Mira el antes y después.",
        "Miles de clientes ya lo tienen.",
    ],
)
def test_testimonials_and_sales_claims_are_refused(text: str) -> None:
    problems = check_copy(f"{text} {WA_LINK}", {85_000})
    assert any("testimonio" in problem for problem in problems)


def test_a_plain_honest_text_passes() -> None:
    assert (
        check_copy(f"¿Buscas un regalo hecho a mano? Este bolso vale $85.000. {WA_LINK}", {85_000})
        == []
    )
