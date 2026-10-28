"""T070: the redacted dataset, the Wonder call under BriefSchema and the checks outside it."""

import json
from pathlib import Path

import pytest

from vitrina_core.jobs import JobFailedError
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import CallMeta, ChatRequest, ChatResponse
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import REPO_ROOT, Settings
from vitrina_learning.briefs import (
    BRIEF_OUTPUT_SCHEMA,
    BriefContent,
    brief_request,
    write_brief,
)
from vitrina_learning.testing import BAG, NOT_OURS, fixture_dataset

EXAMPLE = (
    "El ángulo 'hecho a mano' generó 3× más chats, pero el 40 % se frenó por el envío: "
    "el próximo lote abre con envío gratis en Bogotá."
)


def answer(**changes: object) -> str:
    brief: dict[str, object] = {
        "headline": EXAMPLE,
        "insights": [
            {
                "text": "Hecho a mano trae 3 veces más chats que precio.",
                "evidence": [{"metric": "chats_handmade", "value": 12}],
            }
        ],
        "recommended_angles": [{"angle": "handmade", "rationale": "Más chats y reservas."}],
        "creative_directives": [
            {
                "product_id": str(BAG),
                "angle": "handmade",
                "hook": "story",
                "scene_brief": "La mochila sobre un telar, luz de mañana.",
                "offer": "Envío gratis en Bogotá",
            }
        ],
        "reactivation_segments": [
            {"objection_tag": "shipping", "message_angle": "envío gratis", "suggested_ad_id": None}
        ],
    }
    brief.update(changes)
    return json.dumps(brief, ensure_ascii=False)


class Wonder:
    """A ``ChatModel`` that answers from a script and keeps the requests."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        from vitrina_core.providers import ChatMessage

        return ChatResponse(
            message=ChatMessage(role="assistant", content=self.answers.pop(0)),
            finish_reason="stop",
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose="brief"),
        )


def test_fr_050_the_dataset_carries_no_customer_text_and_has_a_stable_hash() -> None:
    dataset = fixture_dataset()
    text = dataset.model_dump_json()

    assert "+57" not in text  # redacted product names, no customer text
    assert "@" not in text
    assert dataset.digest() == fixture_dataset().digest()
    assert len(dataset.digest()) == 64


def test_fr_050_wonder_gets_the_dataset_under_a_strict_brief_schema() -> None:
    request = brief_request(fixture_dataset(), model="wonder-test")

    assert (request.model, request.purpose) == ("wonder-test", "brief")
    assert request.response_format is not None
    assert request.response_format.strict is True
    assert request.response_format.json_schema == BRIEF_OUTPUT_SCHEMA
    assert json.loads(str(request.messages[-1].content)) == json.loads(
        fixture_dataset().model_dump_json()
    )


async def test_as_5_1_a_valid_answer_becomes_the_brief_with_evidence_as_numbers() -> None:
    wonder = Wonder(answer())

    content, calls = await write_brief(wonder, fixture_dataset(), model="wonder-test")

    assert content.insights[0].evidence == {"chats_handmade": 12}
    assert content.creative_directives[0].product_id == BAG
    assert content.reactivation_segments[0].suggested_ad_id is None
    assert len(calls) == 1


def test_as_5_3_the_example_headline_fits_the_brief() -> None:
    content = BriefContent.model_validate(
        {
            "headline": EXAMPLE,
            "insights": [],
            "recommended_angles": [],
            "creative_directives": [],
            "reactivation_segments": [],
        }
    )

    assert content.headline == EXAMPLE


async def test_fr_050_a_product_that_is_not_the_sellers_gets_one_retry_then_fails() -> None:
    stranger = {
        "product_id": str(NOT_OURS),
        "angle": "price",
        "hook": None,
        "scene_brief": "Otra cosa.",
        "offer": None,
    }
    wonder = Wonder(answer(creative_directives=[stranger]), answer(creative_directives=[stranger]))

    with pytest.raises(JobFailedError) as failure:
        await write_brief(wonder, fixture_dataset(), model="wonder-test")

    assert failure.value.code == "BRIEF_SCHEMA_INVALID"
    assert len(wonder.requests) == 2
    assert "product_id" in str(wonder.requests[1].messages[-1].content)


async def test_fr_050_a_broken_answer_is_asked_once_more_and_the_second_one_counts() -> None:
    wonder = Wonder("not json at all", answer())

    content, calls = await write_brief(wonder, fixture_dataset(), model="wonder-test")

    assert content.headline == EXAMPLE
    assert len(calls) == 2


@pytest.mark.parametrize("missing", ["headline", "insights"])
async def test_fr_050_an_answer_without_a_required_part_fails(missing: str) -> None:
    broken = json.loads(answer())
    del broken[missing]
    wonder = Wonder(json.dumps(broken), json.dumps(broken))

    with pytest.raises(JobFailedError, match=r"brief did not validate"):
        await write_brief(wonder, fixture_dataset(), model="wonder-test")


async def test_as_5_1_wonder_writes_a_valid_brief_from_the_recorded_dataset(
    tmp_path: Path,
) -> None:
    # One live call to muse-wonder-1.0, recorded as a cassette (T070); replayed here.
    settings = Settings(_env_file=None, cassette_dir=REPO_ROOT / "evals" / "cassettes")  # type: ignore[call-arg]
    providers = compose(settings, InMemoryLedger(), mode=ProviderMode.replay)

    content, calls = await write_brief(providers.chat, fixture_dataset(), model="muse-wonder-1.0")

    assert content.headline
    assert len(content.headline) <= 300
    assert content.insights
    assert all(insight.evidence for insight in content.insights)
    assert {directive.product_id for directive in content.creative_directives} <= {
        product.id for product in fixture_dataset().products
    }
    assert calls[0].model == "muse-wonder-1.0"


@pytest.mark.parametrize(
    ("value", "grounded"),
    [(12, True), (0.25, True), (3, True), (75.0, True), (8, True), (1234567.89, False)],
)
async def test_fr_050_evidence_must_be_numbers_from_the_data(value: float, grounded: bool) -> None:
    # 12 and 0.25 are in the data; 3 = 12 / 4, 75.0 % = 6 / 8 and 8 = 12 - 4 derive from it.
    insight = {"text": "Un hallazgo.", "evidence": [{"metric": "m", "value": value}]}
    wonder = Wonder(answer(insights=[insight]), answer())

    _, calls = await write_brief(wonder, fixture_dataset(), model="wonder-test")

    assert len(calls) == (1 if grounded else 2)
    if not grounded:
        assert "1234567.89" in str(wonder.requests[1].messages[-1].content)


async def test_fr_050_a_brief_without_insights_gets_one_retry() -> None:
    wonder = Wonder(answer(insights=[]), answer())

    _, calls = await write_brief(wonder, fixture_dataset(), model="wonder-test")

    assert len(calls) == 2
