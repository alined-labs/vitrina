"""Brand-voice judge (T041): does each copy text sound like the seller? (AS-2.4, FR-011)

The judge scores five rubric dimensions from 1 to 5 against the seller's brand-voice profile;
the score is their mean, computed here rather than trusted from the model. Muse Glimmer (open
weights, on Modal) is the judge once it is hosted (Q05); until then Spark judges with the same
rubric, and ``brand_voice_judge`` records which one did. Texts below the seller's minimum
(``brand_voice_min``, default 3.5) are rewritten once with the voice feedback and re-scored;
still below, they are kept as ``dropped_low_voice`` for audit. Scoring is a quality gate, not a
safety gate: when the judge cannot answer, texts stay candidates with no score (judge ``none``).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from vitrina_core.money import amounts_cop
from vitrina_core.providers import ChatMessage, ChatModel, ChatRequest, ResponseFormat, with_backoff
from vitrina_creative.copy import (
    BrandVoice,
    CopyAngle,
    CopyDraft,
    CopyHook,
    CopyInput,
    check_copy,
    rewrite_cells,
)

JudgeName = Literal["glimmer", "spark_rubric", "none"]
Status = Literal["candidate", "dropped_low_voice"]
DIMENSIONS = ("formality", "warmth", "emoji", "regionalisms", "naturalness")
BRAND_VOICE_MIN = 3.5

RUBRIC = """Eres un evaluador de la voz de marca de un negocio pequeño en Colombia. Calificas si cada texto suena como ese negocio.

PERFIL DE VOZ DEL NEGOCIO
Formalidad {formality}/5 (1 muy informal, 5 muy formal) · Calidez {warmth}/5 · Emojis: {emoji_rate} por texto · Regionalismos que usa: {regionalisms}

Para cada texto, califica de 1 a 5 (5 = igual al perfil):
- formality: qué tan cerca está su formalidad de la del negocio
- warmth: qué tan cerca está su calidez de la del negocio
- emoji: si usa emojis en la medida del negocio
- regionalisms: si usa los regionalismos del negocio, o al menos no inventa otros
- naturalness: si suena escrito por una persona del negocio y no por una máquina

Responde solo con JSON y nada más:
{{"scores": [{{"index": 1, "formality": 4, "warmth": 5, "emoji": 3, "regionalisms": 4, "naturalness": 4}}]}}"""


class VoicedVariant(BaseModel):
    """A copy text ready to store as an openapi ``CopyVariant`` (id and job_id come with T034)."""

    hook: CopyHook
    angle: CopyAngle
    text: str = Field(max_length=300)
    brand_voice_score: float | None = Field(default=None, ge=1, le=5)
    brand_voice_judge: JudgeName
    status: Status


@dataclass(frozen=True)
class RubricJudge:
    """Scores texts with the rubric through any chat model: Spark now, Glimmer once hosted."""

    chat: ChatModel
    model: str
    name: Literal["glimmer", "spark_rubric"] = "spark_rubric"

    async def score(self, texts: Sequence[str], voice: BrandVoice) -> list[float | None]:
        """One score per text (mean of the five dimensions), None where the judge failed."""
        if not texts:
            return []
        request = self._request(texts, voice)
        for _attempt in range(2):  # one retry on unusable output
            response = await with_backoff(lambda: self.chat.complete(request))
            scores = _parse_scores(response.message.content, len(texts))
            if scores is not None:
                return scores
        return [None] * len(texts)

    def _request(self, texts: Sequence[str], voice: BrandVoice) -> ChatRequest:
        listing = "\n".join(f"{index}. {text}" for index, text in enumerate(texts, start=1))
        dimension = {"type": "integer", "minimum": 1, "maximum": 5}
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["scores"],
            "properties": {
                "scores": {
                    "type": "array",
                    "minItems": len(texts),
                    "maxItems": len(texts),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["index", *DIMENSIONS],
                        "properties": {
                            "index": {"type": "integer"},
                            **dict.fromkeys(DIMENSIONS, dimension),
                        },
                    },
                }
            },
        }
        system = RUBRIC.format(
            formality=f"{voice.formality:g}",
            warmth=f"{voice.warmth:g}",
            emoji_rate=f"{voice.emoji_rate:g}",
            regionalisms=", ".join(voice.regionalisms) or "ninguno",
        )
        return ChatRequest(
            model=self.model,
            purpose="judge",
            messages=(
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=f"TEXTOS\n{listing}"),
            ),
            response_format=ResponseFormat(name="brand_voice_scores", json_schema=schema),
            temperature=0.0,
        )


async def judge_copy(
    item: CopyInput,
    drafts: Sequence[CopyDraft],
    *,
    judge: RubricJudge,
    writer: ChatModel,
    writer_model: str,
    minimum: float = BRAND_VOICE_MIN,
) -> list[VoicedVariant]:
    """Score every draft; rewrite those below ``minimum`` once; keep the rest for audit."""
    texts = [draft.text for draft in drafts]
    scores = await judge.score(texts, item.voice)
    if all(score is None for score in scores):  # the judge could not answer: no gate
        return [_voiced(draft, draft.text, None, "none", "candidate") for draft in drafts]

    low = [index for index, score in enumerate(scores) if score is not None and score < minimum]
    if low:
        feedback = [
            [f"la voz no suena al negocio (puntaje {scores[index]:.1f} de 5, mínimo {minimum:g})"]
            for index in low
        ]
        cells = [(drafts[index].hook, drafts[index].angle) for index in low]
        rewritten = await rewrite_cells(item, cells, feedback, writer=writer, model=writer_model)
        allowed = {item.price_cop, *(amount for fact in item.facts for amount in amounts_cop(fact))}
        candidates = {
            index: rewritten[position]
            for position, index in enumerate(low)
            if position in rewritten and not check_copy(rewritten[position], allowed)
        }
        if candidates:
            rescored = await judge.score(list(candidates.values()), item.voice)
            for (index, text), score in zip(candidates.items(), rescored, strict=True):
                if score is not None and score > (scores[index] or 0):
                    texts[index], scores[index] = text, score

    voiced: list[VoicedVariant] = []
    for index, draft in enumerate(drafts):
        score = scores[index]
        if score is None:
            voiced.append(_voiced(draft, texts[index], None, "none", "candidate"))
        else:
            status: Status = "dropped_low_voice" if score < minimum else "candidate"
            voiced.append(_voiced(draft, texts[index], score, judge.name, status))
    return voiced


def _voiced(
    draft: CopyDraft, text: str, score: float | None, judge: JudgeName, status: Status
) -> VoicedVariant:
    return VoicedVariant(
        hook=draft.hook,
        angle=draft.angle,
        text=text,
        brand_voice_score=round(score, 2) if score is not None else None,
        brand_voice_judge=judge,
        status=status,
    )


def _parse_scores(content: object, count: int) -> list[float | None] | None:
    """Mean of the five dimensions per text (clamped to 1–5), or None when unusable."""
    if not isinstance(content, str):
        return None
    start, end = content.find("{"), content.rfind("}")
    try:
        data = json.loads(content[start : end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        return None
    entries = data.get("scores") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    scores: list[float | None] = [None] * count
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), int):
            continue
        index = entry["index"] - 1
        numbers = [entry.get(name) for name in DIMENSIONS]
        values = [
            min(5.0, max(1.0, float(number)))
            for number in numbers
            if isinstance(number, int | float) and not isinstance(number, bool)
        ]
        if 0 <= index < count and len(values) == len(DIMENSIONS):
            scores[index] = sum(values) / len(values)
    return scores if any(score is not None for score in scores) else None
