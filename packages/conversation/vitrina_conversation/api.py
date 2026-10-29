"""Public interface of the conversation module (constitution Art. VI.3).

``run_turn`` runs one agent turn (T024): retrieval, the Muse Spark tool loop and the checks
outside the model (R2–R5). ``process_inbound`` takes one customer message from WhatsApp to the
paced reply (T027), on a ``ConversationStore`` (in memory or Postgres). ``agent_turn_result``
maps a turn to the ``POST /agent/turns`` response (FR-030).
"""

from vitrina_conversation.attribution import AdLookup, Attribution
from vitrina_conversation.booking import BookingStore, InMemoryBookings, PostgresBookings
from vitrina_conversation.campaigns import PostgresCandidates, campaign_deps
from vitrina_conversation.contract import AgentTurnOutput, HandoffReason, ObjectionTag
from vitrina_conversation.engine import (
    AgentDeps,
    Exchange,
    TurnRequest,
    TurnResult,
    parse_output,
    run_turn,
)
from vitrina_conversation.ingest import (
    InboundQueue,
    Ingest,
    IngestResult,
    InvalidSignatureError,
    PayloadTooLargeError,
    SellerResolver,
)
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.pipeline import Inbound, InboundOutcome, PipelineDeps, process_inbound
from vitrina_conversation.runtime import bookings, conversation_store, messenger, pacer
from vitrina_conversation.schemas import (
    AgentTurnRequest,
    AgentTurnResult,
    Conversation,
    HumanMessageCreate,
    Message,
    agent_turn_result,
    conversation_view,
    message_view,
)
from vitrina_conversation.store import (
    ConversationRecord,
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
from vitrina_conversation.validation import FALLBACK_REPLY, Agreement, Review, agreed_offers
from vitrina_conversation.wiring import (
    PROCESS_INBOUND,
    BackgroundInboundQueue,
    PostgresInboundQueue,
    agent_deps,
    memory_pipeline,
    messenger_for,
    postgres_pipeline,
    process_stored_message,
    sellers_in_memory,
    sellers_on_postgres,
)

__all__ = [
    "FALLBACK_REPLY",
    "PROCESS_INBOUND",
    "AdLookup",
    "AgentDeps",
    "AgentTurnOutput",
    "AgentTurnRequest",
    "AgentTurnResult",
    "Agreement",
    "ApprovedAds",
    "Attribution",
    "BackgroundInboundQueue",
    "BookingStore",
    "ConsentAction",
    "Conversation",
    "ConversationRecord",
    "ConversationState",
    "ConversationStore",
    "Exchange",
    "HandoffAction",
    "HandoffReason",
    "HumanMessageCreate",
    "InMemoryBookings",
    "InMemoryConversationStore",
    "Inbound",
    "InboundOutcome",
    "InboundQueue",
    "Ingest",
    "IngestResult",
    "InvalidSignatureError",
    "Message",
    "ObjectionAction",
    "ObjectionTag",
    "OptOutAction",
    "Pacer",
    "PayloadTooLargeError",
    "PipelineDeps",
    "PostgresBookings",
    "PostgresCandidates",
    "PostgresConversationStore",
    "PostgresInboundQueue",
    "ReservedItem",
    "Review",
    "SellerResolver",
    "StoredMessage",
    "ToolCallRecord",
    "TurnActions",
    "TurnRequest",
    "TurnResult",
    "agent_deps",
    "agent_turn_result",
    "agreed_offers",
    "bookings",
    "campaign_deps",
    "conversation_store",
    "conversation_view",
    "memory_pipeline",
    "message_view",
    "messenger",
    "messenger_for",
    "pacer",
    "parse_output",
    "postgres_pipeline",
    "process_inbound",
    "process_stored_message",
    "run_turn",
    "sellers_in_memory",
    "sellers_on_postgres",
]
