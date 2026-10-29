"""Foto → Vitrina: one Spark yes/no question on each upload, before anything else (AS-8.2)."""

import json

from vitrina_core.providers import ImagePart
from vitrina_creative.showcase import upload_allowed
from vitrina_creative.testing import ScriptedAnswers, source_photo


def verdict(allowed: bool) -> str:
    return json.dumps({"allowed": allowed})


async def test_an_upload_is_one_yes_no_question_about_the_photo_alone() -> None:
    photo = source_photo().image
    chat = ScriptedAnswers(verdict(True))

    assert await upload_allowed(photo, chat=chat, model="spark-test") is True

    (request,) = chat.requests
    assert (request.model, request.purpose) == ("spark-test", "classify")
    assert (request.temperature, request.reasoning_effort) == (0.0, "minimal")
    assert request.response_format is not None
    assert request.response_format.json_schema["required"] == ["allowed"]
    content = request.messages[-1].content
    assert not isinstance(content, str)
    assert [part.image for part in content if isinstance(part, ImagePart)] == [photo]


async def test_a_no_refuses_the_upload() -> None:
    chat = ScriptedAnswers(verdict(False))

    assert await upload_allowed(source_photo().image, chat=chat, model="m") is False


async def test_two_unusable_answers_refuse_and_one_is_asked_again() -> None:
    refused = ScriptedAnswers("no sé", "{}")
    assert await upload_allowed(source_photo().image, chat=refused, model="m") is False

    retried = ScriptedAnswers("no sé", f"```json\n{verdict(True)}\n```")
    assert await upload_allowed(source_photo().image, chat=retried, model="m") is True
    assert len(retried.requests) == 2
