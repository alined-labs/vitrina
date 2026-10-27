"""The agent's frozen contract, read from ``contracts/agent-tools.json`` (FR-024, FR-025).

Tools reach the model verbatim (their ``parameters`` untouched) and the final answer must be
an ``AgentTurnOutput``: the JSON schema goes to ``response_format`` as written, and the
Pydantic model below parses the answer. A test keeps the model equal to the schema; the file
is the source of truth (constitution Art. VI.1).
"""

import json
from functools import cache
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vitrina_core.providers import ToolSpec
from vitrina_core.settings import REPO_ROOT

AGENT_TOOLS = REPO_ROOT / "specs" / "001-vitrina-api" / "contracts" / "agent-tools.json"

HandoffReason = Literal[
    "customer_request",
    "complaint_refund",
    "payment_issue",
    "medical_topic",
    "low_confidence_twice",
    "negative_sentiment",
    "amount_over_threshold",
    "price_unverified",
    "policy_exception",
    "other",
]
ObjectionTag = Literal[
    "price", "shipping", "size", "availability", "trust", "timing", "payment", "off_topic", "other"
]


class AgentTurnOutput(BaseModel):
    """The only shape the agent may return as its final answer (FR-025)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reply: Annotated[str, Field(max_length=1200)]
    cited_fact_ids: tuple[UUID, ...]
    confidence: Annotated[float, Field(ge=0, le=1)]
    handoff_reason: HandoffReason | None
    objection_tag: ObjectionTag | None
    proposed_consent_prompt: bool


@cache
def _definitions() -> dict[str, Any]:
    definitions: dict[str, Any] = json.loads(AGENT_TOOLS.read_text(encoding="utf-8"))["definitions"]
    return definitions


def output_schema() -> dict[str, Any]:
    """``AgentTurnOutput`` exactly as the contract writes it, for ``response_format``."""
    schema: dict[str, Any] = _definitions()["AgentTurnOutput"]
    return schema


@cache
def tool_specs() -> tuple[ToolSpec, ...]:
    """Every contract tool in its order, ``parameters`` passed verbatim (agent-tools.json)."""
    return tuple(
        ToolSpec(
            name=tool["function"]["name"],
            description=tool["function"]["description"],
            parameters=tool["function"]["parameters"],
        )
        for tool in _definitions()["tools"]
    )
