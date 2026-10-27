"""The agent the conversations_es suite evaluates: MX2's conversation engine (T024).

``conversation_agent`` adapts ``vitrina_conversation.api.run_turn`` to the evals' ``Agent``
protocol once that function exists; until then it returns None and every run reports why,
instead of scoring a stand-in (Art. VII.3: the metrics page shows only real runs).
"""

from vitrina_conversation import api as conversation
from vitrina_evals.runner import Agent


def conversation_agent() -> Agent | None:
    if not hasattr(conversation, "run_turn"):
        return None
    raise NotImplementedError(
        "conversation.api.run_turn exists: write its adapter to the Agent protocol (T028)"
    )
