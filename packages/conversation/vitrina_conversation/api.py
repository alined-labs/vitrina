"""Public interface of the conversation module (constitution Art. VI.3).

``run_turn`` runs one agent turn (T024): retrieval, the Muse Spark tool loop and the checks
outside the model (R2–R5). The webhook ingest (``process_inbound``) and paced sending
(``send_outbound``) join with T027.
"""

from vitrina_conversation.contract import AgentTurnOutput, HandoffReason, ObjectionTag
from vitrina_conversation.engine import (
    AgentDeps,
    Exchange,
    TurnRequest,
    TurnResult,
    parse_output,
    run_turn,
)
from vitrina_conversation.tools import (
    ApprovedAds,
    ConsentAction,
    HandoffAction,
    ObjectionAction,
    OptOutAction,
    ToolCallRecord,
    TurnActions,
)
from vitrina_conversation.validation import FALLBACK_REPLY, Review

__all__ = [
    "FALLBACK_REPLY",
    "AgentDeps",
    "AgentTurnOutput",
    "ApprovedAds",
    "ConsentAction",
    "Exchange",
    "HandoffAction",
    "HandoffReason",
    "ObjectionAction",
    "ObjectionTag",
    "OptOutAction",
    "Review",
    "ToolCallRecord",
    "TurnActions",
    "TurnRequest",
    "TurnResult",
    "parse_output",
    "run_turn",
]
