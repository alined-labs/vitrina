"""T041 brand-voice judge: rubric mean, one voice rewrite, drops kept for audit (AS-2.4)."""

import json
import re

from vitrina_core.providers import CallMeta, ChatMessage, ChatRequest, ChatResponse
from vitrina_creative.api import (
    BrandVoice,
    CopyDraft,
    CopyInput,
    RubricJudge,
    judge_copy,
)
from vitrina_creative.copy import WA_LINK

ITEM = CopyInput(
    business_name="Tejidos Vecina",
    city="Medellín",
    product_name="Bolso tejido a mano",
    product_description="Bolso en hilo de algodón, tejido a mano.",
    price_cop=85_000,
    stock=3,
    voice=BrandVoice(formality=2, warmth=5, emoji_rate=0.5, regionalisms=("veci",)),
)
DRAFTS = [
    CopyDraft(hook="question", angle="price", text=f"¿Te gusta, veci? Vale $85.000. {WA_LINK}"),
    CopyDraft(hook="benefit", angle="quality", text=f"Estimado cliente, adquiera hoy. {WA_LINK}"),
    CopyDraft(hook="story", angle="gift", text=f"Lo tejí pensando en ti, veci. {WA_LINK}"),
]
NUMBERED = re.compile(r"^(\d+)\. (.*)$", re.MULTILINE)


def rubric(value: int) -> dict[str, int]:
    return dict.fromkeys(("formality", "warmth", "emoji", "regionalisms", "naturalness"), value)


class ScriptedChat:
    """Replies in order with the given callables of the request (or raw strings)."""

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        text = reply(request) if callable(reply) else reply
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return ChatResponse(message=ChatMessage(role="assistant", content=str(text)), meta=meta)


def scores_for(values: list[int]):  # one rubric value per numbered text in the request
    def reply(request: ChatRequest) -> str:
        user = request.messages[1].content
        assert isinstance(user, str)
        count = len(NUMBERED.findall(user))
        items = [{"index": i + 1, **rubric(values[i])} for i in range(count)]
        return json.dumps({"scores": items})

    return reply


async def test_as_2_4_every_text_gets_a_1_to_5_score_from_the_rubric_mean() -> None:
    chat = ScriptedChat(json.dumps({"scores": [
        {"index": 1, "formality": 5, "warmth": 5, "emoji": 4, "regionalisms": 5, "naturalness": 4},
        {"index": 2, "formality": 4, "warmth": 4, "emoji": 4, "regionalisms": 4, "naturalness": 4},
        {"index": 3, "formality": 9, "warmth": 5, "emoji": 5, "regionalisms": 5, "naturalness": 5},
    ]}))  # fmt: skip
    judge = RubricJudge(chat=chat, model="judge-model")
    assert await judge.score([d.text for d in DRAFTS], ITEM.voice) == [4.6, 4.0, 5.0]  # 9 clamped
    (request,) = chat.requests
    assert (request.model, request.purpose, request.temperature) == ("judge-model", "judge", 0.0)
    system = request.messages[0].content
    assert isinstance(system, str)
    assert "Formalidad 2/5" in system
    assert "veci" in system


async def test_as_2_4_texts_below_the_minimum_are_rewritten_once_and_kept_when_better() -> None:
    judge = RubricJudge(chat=ScriptedChat(scores_for([5, 2, 4]), scores_for([4])), model="judge")
    writer = ScriptedChat(
        json.dumps(
            {"variants": [{"slot": 1, "text": f"Veci, este bolso te enamora: $85.000. {WA_LINK}"}]}
        )
    )
    voiced = await judge_copy(ITEM, DRAFTS, judge=judge, writer=writer, writer_model="copy-model")
    assert [v.status for v in voiced] == ["candidate", "candidate", "candidate"]
    assert voiced[1].text.startswith("Veci, este bolso")
    assert voiced[1].brand_voice_score == 4.0
    assert voiced[1].brand_voice_judge == "spark_rubric"
    (rewrite,) = writer.requests
    user = rewrite.messages[1].content
    assert isinstance(user, str)
    assert "la voz no suena al negocio (puntaje 2.0 de 5, mínimo 3.5)" in user
    assert "hook=benefit, angle=quality" in user


async def test_as_2_4_still_below_after_the_rewrite_is_dropped_but_kept_for_audit() -> None:
    judge = RubricJudge(chat=ScriptedChat(scores_for([5, 2, 4]), scores_for([3])), model="judge")
    writer = ScriptedChat(
        json.dumps({"variants": [{"slot": 1, "text": f"Señor usuario: compre. {WA_LINK}"}]})
    )
    voiced = await judge_copy(ITEM, DRAFTS, judge=judge, writer=writer, writer_model="m")
    assert voiced[1].status == "dropped_low_voice"
    assert voiced[1].brand_voice_score == 3.0  # the better of the two attempts is kept
    assert voiced[1].text.startswith("Señor usuario")


async def test_a_rewrite_that_breaks_the_copy_rules_is_ignored() -> None:
    judge = RubricJudge(chat=ScriptedChat(scores_for([5, 2, 4])), model="judge")
    writer = ScriptedChat(json.dumps({"variants": [{"slot": 1, "text": "Veci, ahora a $70.000"}]}))
    voiced = await judge_copy(ITEM, DRAFTS, judge=judge, writer=writer, writer_model="m")
    assert voiced[1].text == DRAFTS[1].text  # no link and an invented price: not used
    assert voiced[1].status == "dropped_low_voice"


async def test_the_seller_minimum_decides_who_is_rewritten() -> None:
    judge = RubricJudge(chat=ScriptedChat(scores_for([5, 4, 4])), model="judge")
    writer = ScriptedChat("{}")
    voiced = await judge_copy(
        ITEM, DRAFTS, judge=judge, writer=writer, writer_model="m", minimum=4.5
    )
    assert [v.status for v in voiced] == ["candidate", "dropped_low_voice", "dropped_low_voice"]
    assert len(writer.requests) == 1  # one rewrite call for both low texts


async def test_an_unusable_judge_leaves_texts_unscored_candidates() -> None:
    judge = RubricJudge(chat=ScriptedChat("no puedo", "tampoco"), model="judge")
    writer = ScriptedChat("{}")
    voiced = await judge_copy(ITEM, DRAFTS, judge=judge, writer=writer, writer_model="m")
    assert all(v.status == "candidate" for v in voiced)
    assert all((v.brand_voice_score, v.brand_voice_judge) == (None, "none") for v in voiced)
    assert writer.requests == []


async def test_glimmer_is_the_same_rubric_through_another_model() -> None:
    judge = RubricJudge(
        chat=ScriptedChat(scores_for([4, 4, 4])), model="glimmer-model", name="glimmer"
    )
    voiced = await judge_copy(
        ITEM, DRAFTS, judge=judge, writer=ScriptedChat("{}"), writer_model="m"
    )
    assert {v.brand_voice_judge for v in voiced} == {"glimmer"}
