"""Public interface of the conversation module (constitution Art. VI.3).

``run_turn`` runs one agent turn (T024): retrieval, the Muse Spark tool loop and the checks
outside the model (R2–R5). ``process_inbound`` takes one customer message from WhatsApp to the
paced reply (T027), on a ``ConversationStore`` (in memory or Postgres). ``agent_turn_result``
maps a turn to the ``POST /agent/turns`` response (FR-030).
"""

from vitrina_conversation.attribution import AdLookup, Attribution
from vitrina_conversation.contract import AgentTurnOutput, HandoffReason, ObjectionTag
from vitrina_conversation.engine import (
    AgentDeps,
    Exchange,
    TurnRequest,
    TurnResult,
    parse_output,
    run_turn,
)
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.pipeline import Inbound, InboundOutcome, PipelineDeps, process_inbound
from vitrina_conversation.schemas import AgentTurnRequest, AgentTurnResult, agent_turn_result
from vitrina_conversation.store import (
    ConversationState,
    ConversationStore,
    InMemoryConversationStore,
    PostgresConversationStore,
    StoredMessage,
)
from vitrina_conversation.tools import (
    ApprovedAds,
    ConsentAction,
    HandoffAction,
    ObjectionAction,
    OptOutAction,
    ReservedItem,
    ToolCallRecord,
    TurnActions,
)
from vitrina_conversation.validation import FALLBACK_REPLY, Review

__all__ = [
    "FALLBACK_REPLY",
    "AdLookup",
    "AgentDeps",
    "AgentTurnOutput",
    "AgentTurnRequest",
    "AgentTurnResult",
    "ApprovedAds",
    "Attribution",
    "ConsentAction",
    "ConversationState",
    "ConversationStore",
    "Exchange",
    "HandoffAction",
    "HandoffReason",
    "InMemoryConversationStore",
    "Inbound",
    "InboundOutcome",
    "ObjectionAction",
    "ObjectionTag",
    "OptOutAction",
    "Pacer",
    "PipelineDeps",
    "PostgresConversationStore",
    "ReservedItem",
    "Review",
    "StoredMessage",
    "ToolCallRecord",
    "TurnActions",
    "TurnRequest",
    "TurnResult",
    "agent_turn_result",
    "parse_output",
    "process_inbound",
    "run_turn",
]
