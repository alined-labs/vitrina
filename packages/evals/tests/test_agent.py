"""T028: the conversations_es suite drives the real engine through ``run_turn`` (AS-7.1)."""

from pathlib import Path

from vitrina_conversation.testing import ScriptedChat, final_answer
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_evals.agent import ConversationAgent, conversation_agent
from vitrina_evals.cases import build_scene, fixture_id, load_business
from vitrina_evals.runner import AgentTurn, Exchange


def settings(tmp_path: Path, **values: object) -> Settings:
    return Settings(_env_file=None, cassette_dir=tmp_path, **values)  # type: ignore[call-arg]


def test_without_model_agent_there_is_no_agent(tmp_path: Path) -> None:
    assert conversation_agent(settings(tmp_path)) is None


async def test_as_7_1_a_case_runs_through_run_turn_with_its_citations(tmp_path: Path) -> None:
    config = settings(tmp_path, model_agent="spark-test")
    scene = await build_scene(load_business("mochilas_ana"))
    item = fixture_id("mochilas_ana", "mochila-azul")
    chat = ScriptedChat([final_answer("La mochila azul vale $85.000.", item)])
    providers = compose(config, InMemoryLedger(), mode=ProviderMode.replay)
    agent = conversation_agent(config)
    assert isinstance(agent, ConversationAgent)

    outcome = await agent.run_turn(
        AgentTurn(
            scene.seller,
            scene.catalog,
            providers.__class__(**{**providers.__dict__, "chat": chat}),
            (Exchange("Hola", "¡Hola! ¿En qué te ayudo?"),),
            "¿cuánto vale la mochila azul?",
        )
    )

    assert (outcome.reply, outcome.cited_ids, outcome.handoff_reason) == (
        "La mochila azul vale $85.000.",
        (item,),
        None,
    )
    assert chat.requests[0].model == "spark-test"
