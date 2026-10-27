"""FR-024, FR-025: tools reach the model verbatim and the output model mirrors agent-tools.json."""

import json
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from vitrina_conversation.contract import (
    AGENT_TOOLS,
    AgentTurnOutput,
    HandoffReason,
    ObjectionTag,
    output_schema,
    tool_specs,
)

CONTRACT = json.loads(AGENT_TOOLS.read_text(encoding="utf-8"))["definitions"]


def test_fr_024_every_contract_tool_is_offered_verbatim() -> None:
    contract_tools = [tool["function"] for tool in CONTRACT["tools"]]

    specs = tool_specs()

    assert [spec.name for spec in specs] == [tool["name"] for tool in contract_tools]
    for spec, tool in zip(specs, contract_tools, strict=True):
        assert (spec.description, spec.parameters) == (tool["description"], tool["parameters"])


def test_fr_025_response_format_is_the_contract_schema() -> None:
    assert output_schema() == CONTRACT["AgentTurnOutput"]


def test_fr_025_output_model_mirrors_the_contract() -> None:
    schema = CONTRACT["AgentTurnOutput"]
    properties = schema["properties"]

    model = AgentTurnOutput.model_json_schema()

    assert set(model["required"]) == set(schema["required"])
    assert model["additionalProperties"] is False
    assert set(get_args(HandoffReason)) == set(properties["handoff_reason"]["enum"]) - {None}
    assert set(get_args(ObjectionTag)) == set(properties["objection_tag"]["enum"]) - {None}
    assert model["properties"]["reply"]["maxLength"] == properties["reply"]["maxLength"]
    confidence = model["properties"]["confidence"]
    assert (confidence["minimum"], confidence["maximum"]) == (0, 1)


def valid_output(**changes: object) -> dict[str, object]:
    return {
        "reply": "El envío a Medellín vale $12.000.",
        "cited_fact_ids": [str(uuid4())],
        "confidence": 0.9,
        "handoff_reason": None,
        "objection_tag": None,
        "proposed_consent_prompt": False,
        **changes,
    }


def test_fr_025_a_contract_answer_parses() -> None:
    output = AgentTurnOutput.model_validate(valid_output(handoff_reason="customer_request"))

    assert output.handoff_reason == "customer_request"


@pytest.mark.parametrize(
    "changes",
    [
        {"reply": "x" * 1201},
        {"confidence": 1.5},
        {"handoff_reason": "tired"},
        {"objection_tag": "color"},
        {"cited_fact_ids": ["not-a-uuid"]},
        {"extra": "field"},
    ],
    ids=["long-reply", "confidence", "handoff-reason", "objection-tag", "fact-id", "extra"],
)
def test_fr_025_answers_outside_the_contract_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentTurnOutput.model_validate(valid_output(**changes))


def test_fr_025_every_field_is_required() -> None:
    answer = valid_output()
    del answer["objection_tag"]

    with pytest.raises(ValidationError):
        AgentTurnOutput.model_validate(answer)
