"""The agent the conversations_es suite evaluates: the conversation engine (T024).

``conversation_agent`` adapts ``vitrina_conversation.api.run_turn`` to the evals' ``Agent``
protocol: one dry-run turn per customer message, with the case's earlier exchanges as
history and the low-confidence streak carried between them (R4). Without ``MODEL_AGENT``
there is no agent, and every run reports why instead of scoring a stand-in (Art. VII.3: the
metrics page shows only real runs).

Replay finds a cassette only for the very same request (AS-7.3), so a case sends the same
requests on every run: the clock the system prompt shows is pinned, the conversation id
comes from the case's first message, and the ids tools mint are seeded by case and turn. The
dependencies are production's (``agent_deps``), only the clock is the eval's.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid5

from vitrina_conversation import api as conversation
from vitrina_core.ids import seeded_ids
from vitrina_core.settings import Settings
from vitrina_evals.runner import Agent, AgentTurn
from vitrina_evals.scoring import TurnOutcome

# A Wednesday, 10:00 in Bogotá: inside opening hours, the same instant on every run.
EVAL_NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
CASE_IDS = UUID("0192f2c1-3a5d-7e10-8b2f-4c6d8e0a1b33")


class ConversationAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.streak = 0
        self.conversation_id = CASE_IDS

    async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
        if not turn.history:  # a new case
            self.streak, self.conversation_id = 0, uuid5(CASE_IDS, turn.message)
        with seeded_ids(f"{self.conversation_id}:{len(turn.history)}"):
            result = await self._run(turn)
        self.streak = result.low_confidence_streak
        return TurnOutcome(
            reply=result.final_reply,
            cited_ids=result.cited_ids,
            handoff_reason=result.handoff.reason if result.handoff else None,
            off_topic=result.off_topic,
        )

    async def _run(self, turn: AgentTurn) -> conversation.TurnResult:
        return await conversation.run_turn(
            conversation.TurnRequest(
                seller=turn.seller,
                conversation_id=self.conversation_id,
                message=turn.message,
                history=tuple(
                    conversation.Exchange(exchange.customer, exchange.reply)
                    for exchange in turn.history
                ),
                dry_run=True,
                low_confidence_streak=self.streak,
            ),
            replace(
                conversation.agent_deps(self.settings, turn.providers.chat, turn.catalog),
                clock=lambda: EVAL_NOW,
            ),
        )


def conversation_agent(settings: Settings) -> Agent | None:
    try:
        settings.model_id("agent")
    except LookupError:  # MODEL_AGENT unset (spec.md §8): nothing real to evaluate
        return None
    return ConversationAgent(settings)
