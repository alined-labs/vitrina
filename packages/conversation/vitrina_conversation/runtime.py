"""The conversation objects an API process keeps on ``app.state``: one each, made on first use.

The webhook and conversation routers share them, so a seller's reply and the agent's go
through the same pacer (R12) and messenger, and, without a database, the same in-memory store.
"""

from typing import Any

from vitrina_conversation.booking import BookingStore, InMemoryBookings, PostgresBookings
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.store import (
    ConversationStore,
    InMemoryConversationStore,
    PostgresConversationStore,
)
from vitrina_conversation.wiring import messenger_for
from vitrina_core.providers import Messenger


def conversation_store(state: Any) -> ConversationStore:
    store: ConversationStore | None = getattr(state, "conversations", None)
    if store is None:
        store = (
            PostgresConversationStore(state.sessions)
            if state.sessions is not None
            else InMemoryConversationStore()
        )
        state.conversations = store
    return store


def bookings(state: Any) -> BookingStore:
    found: BookingStore | None = getattr(state, "bookings", None)
    if found is None:
        found = state.bookings = (
            PostgresBookings(state.sessions) if state.sessions is not None else InMemoryBookings()
        )
    return found


def messenger(state: Any) -> Messenger:
    found: Messenger | None = getattr(state, "messenger", None)
    if found is None:
        found = state.messenger = messenger_for(state.settings)
    return found


def pacer(state: Any) -> Pacer:
    found: Pacer | None = getattr(state, "pacer", None)
    if found is None:
        found = state.pacer = Pacer(state.settings.outbound_spacing_s)
    return found
