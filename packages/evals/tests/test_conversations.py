import contextlib
from pathlib import Path

import pytest

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import ChatMessage, ChatRequest, ProviderError
from vitrina_core.providers.compose import ProviderMode, Providers, compose
from vitrina_core.settings import Settings
from vitrina_evals.cases import (
    ConversationCase,
    Expected,
    Scene,
    build_scene,
    fixture_id,
    load_business,
    load_cases,
)
from vitrina_evals.runner import AgentTurn, Exchange, run_conversations
from vitrina_evals.scoring import TurnOutcome, score_case, summarize

MOCHILAS = "mochilas_ana"

# ── fixtures ──


def test_as_7_1_the_sixty_cases_load_and_cover_every_category() -> None:
    cases = load_cases()

    assert len(cases) == 60
    assert len({case.case_id for case in cases}) == 60
    assert {case.category for case in cases} == {"grounded", "price_trap", "handoff", "off_topic"}
    for case in cases:
        business = load_business(case.business)
        keys = {item.key for item in (*business.products, *business.facts)}
        assert set(case.expected.cites) | set(case.expected.forbidden_cites) <= keys, case.case_id


@pytest.mark.parametrize("sample", [1, 3])
def test_a_sample_takes_the_first_cases_in_file_order(sample: int) -> None:
    assert load_cases(sample=sample) == load_cases()[:sample]


def test_art_v_1_fixtures_use_synthetic_numbers_only() -> None:
    for key in (MOCHILAS, "sazon_de_la_abuela"):
        assert load_business(key).seller["whatsapp_number"].startswith("+57300000")


@pytest.fixture
async def scene() -> Scene:
    return await build_scene(load_business(MOCHILAS))


async def test_a_scene_has_stable_ids_and_the_amounts_each_item_may_quote(scene: Scene) -> None:
    again = await build_scene(load_business(MOCHILAS))

    azul = fixture_id(MOCHILAS, "mochila-azul")
    assert scene.ids["mochila-azul"] == azul == again.ids["mochila-azul"]
    assert scene.allowed[azul] == {85000}
    assert scene.allowed[fixture_id(MOCHILAS, "envio-medellin")] == {8000}
    # Drafts are in the catalog but quote nothing (FR-003).
    assert fixture_id(MOCHILAS, "mochila-edicion-especial") not in scene.allowed
    assert scene.seller.business_name == "Mochilas Ana"
    assert scene.seller.handoff_amount_threshold_cop == 300000


# ── scoring ──


def case(**expected: object) -> ConversationCase:
    return ConversationCase(
        case_id="c00-test",
        category="grounded",
        business=MOCHILAS,
        messages=("¿Cuánto vale la mochila azul?",),
        expected=Expected.model_validate(expected),
    )


def test_as_7_1_a_cited_true_price_is_grounded_and_passes(scene: Scene) -> None:
    azul = scene.ids["mochila-azul"]

    score = score_case(
        case(cites=["mochila-azul"]),
        scene,
        TurnOutcome(reply="La mochila azul vale $85.000, veci.", cited_ids=(azul,)),
    )

    assert score.passed
    assert score.grounded is True
    assert score.invented_amounts == ()


def test_as_7_2_an_amount_no_cited_item_supports_is_invented(scene: Scene) -> None:
    azul = scene.ids["mochila-azul"]

    uncited = score_case(
        case(cites=["mochila-azul"]), scene, TurnOutcome(reply="Te la dejo en 60 mil.")
    )
    discounted = score_case(
        case(cites=["mochila-azul"], forbidden_amounts=[60000]),
        scene,
        TurnOutcome(reply="Vale $85.000, pero a ti te la dejo en 60 mil.", cited_ids=(azul,)),
    )

    assert uncited.invented_amounts == (60000,)
    assert uncited.grounded is False
    assert not uncited.passed
    assert discounted.invented_amounts == (60000,)
    assert not discounted.passed


def trap(*messages: str, **expected: object) -> ConversationCase:
    return ConversationCase(
        case_id="c00-trap",
        category="price_trap",
        business=MOCHILAS,
        messages=messages,
        expected=Expected.model_validate({"handoff": None, **expected}),
    )


def test_as_7_2_a_yes_to_the_customers_own_price_is_invented(scene: Scene) -> None:
    azul, manilla = scene.ids["mochila-azul"], scene.ids["manilla"]
    haggle = trap("¿Me deja la mochila azul en 60 mil?", forbidden_amounts=[60000])
    promo = trap("¿Es cierto que tienen 2x1 en manillas?", forbidden_cites=["promo-borrador"])

    agreed = score_case(
        haggle,
        scene,
        TurnOutcome(reply="¡Claro que sí, veci! Te la dejo en ese precio.", cited_ids=(azul,)),
    )
    honest = score_case(
        haggle, scene, TurnOutcome(reply="La mochila azul vale $85.000.", cited_ids=(azul,))
    )
    two_for_one = score_case(
        promo,
        scene,
        TurnOutcome(reply="¡Sí, veci! Tenemos 2x1 en manillas.", cited_ids=(manilla,)),
    )

    assert agreed.invented_amounts == (60000,)
    assert not agreed.passed
    assert "says yes to the customer's price no cited item supports: $60.000" in agreed.notes
    assert honest.passed
    assert (two_for_one.passed, two_for_one.invented_replies) == (False, 1)
    assert "2x1" in two_for_one.notes


def test_as_7_2_an_invented_price_in_an_earlier_reply_counts(scene: Scene) -> None:
    azul = scene.ids["mochila-azul"]
    chat = trap("¿Cuánto vale la mochila azul?", "Gracias")

    score = score_case(
        chat,
        scene,
        TurnOutcome(reply="¡Con gusto, veci!"),
        earlier=[TurnOutcome(reply="Vale $80.000.", cited_ids=(azul,))],
    )
    metrics = summarize([score], cost_usd=0.0)

    assert not score.passed
    assert score.invented_amounts == (80000,)
    assert (score.replies, score.invented_replies) == (2, 1)
    assert "reply 1: states amounts no cited item supports: 80000" in score.notes
    assert metrics.invented_price_rate == 0.5


def test_fr_003_citing_a_draft_is_neither_allowed_nor_quotable(scene: Scene) -> None:
    draft = scene.ids["mochila-edicion-especial"]

    score = score_case(
        case(forbidden_cites=["mochila-edicion-especial"]),
        scene,
        TurnOutcome(reply="La edición especial vale $120.000.", cited_ids=(draft,)),
    )

    assert not score.passed
    assert score.invented_amounts == (120000,)
    assert "mochila-edicion-especial" in score.notes


def test_a_handoff_must_happen_with_the_expected_reason(scene: Scene) -> None:
    wanted = case(handoff="complaint_refund")

    right = score_case(
        wanted, scene, TurnOutcome(reply="Te comunico con Ana.", handoff_reason="complaint_refund")
    )
    wrong = score_case(
        wanted, scene, TurnOutcome(reply="Te comunico con Ana.", handoff_reason="other")
    )
    missed = score_case(wanted, scene, TurnOutcome(reply="Lo siento mucho."))

    assert right.passed
    assert right.handoff_predicted
    assert not wrong.passed
    assert wrong.handoff_predicted
    assert not missed.passed
    assert not missed.handoff_predicted


def test_off_topic_questions_are_declined_and_shop_questions_answered(scene: Scene) -> None:
    declined = score_case(
        case(off_topic=True),
        scene,
        TurnOutcome(reply="Solo te puedo ayudar con la tienda.", off_topic=True),
    )
    answered = score_case(case(off_topic=True), scene, TurnOutcome(reply="Claro, 2 + 2 = 4."))
    refused_wrongly = score_case(
        case(cites=["mochila-azul"]), scene, TurnOutcome(reply="No puedo.", off_topic=True)
    )

    assert declined.passed
    assert declined.refused
    assert not answered.passed
    assert not refused_wrongly.passed


def test_as_7_1_metrics_summarize_the_cases(scene: Scene) -> None:
    azul = scene.ids["mochila-azul"]
    scores = [
        score_case(
            case(cites=["mochila-azul"]),
            scene,
            TurnOutcome(reply="Vale $85.000.", cited_ids=(azul,), latency_ms=100),
        ),
        score_case(
            case(cites=["mochila-azul"]), scene, TurnOutcome(reply="Vale $60.000.", latency_ms=300)
        ),
        score_case(
            case(handoff="customer_request"),
            scene,
            TurnOutcome(reply="Te comunico.", handoff_reason="customer_request", latency_ms=200),
        ),
        score_case(
            case(handoff=False),
            scene,
            TurnOutcome(reply="Te comunico.", handoff_reason="other", latency_ms=400),
        ),
        score_case(
            case(off_topic=True),
            scene,
            TurnOutcome(reply="Solo temas de la tienda.", off_topic=True, latency_ms=500),
        ),
    ]

    metrics = summarize(scores, cost_usd=0.0123)

    assert metrics.groundedness_rate == 0.5
    assert metrics.invented_price_rate == 0.2
    assert (metrics.handoff_precision, metrics.handoff_recall) == (0.5, 1.0)
    assert metrics.off_topic_refusal_rate == 1.0
    assert (metrics.latency_p50_ms, metrics.latency_p95_ms) == (300, 500)
    assert metrics.cost_usd_total == 0.0123


# ── runner ──


class ScriptedAgent:
    """Answers each message from a script keyed by its text; records every turn."""

    def __init__(self, script: dict[str, TurnOutcome]) -> None:
        self.script = script
        self.turns: list[AgentTurn] = []

    async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
        self.turns.append(turn)
        if turn.message not in self.script:
            raise RuntimeError(f"no scripted answer for {turn.message!r}")
        return self.script[turn.message]


def replay(tmp_path: Path) -> tuple[Providers, InMemoryLedger]:
    ledger = InMemoryLedger()
    settings = Settings(_env_file=None, cassette_dir=tmp_path)  # type: ignore[call-arg]
    return compose(settings, ledger, mode=ProviderMode.replay), ledger


async def test_as_7_1_a_run_scores_each_case_and_passes_without_invented_prices(
    tmp_path: Path,
) -> None:
    first, second = load_cases()[:2]
    agent = ScriptedAgent(
        {
            first.messages[-1]: TurnOutcome(
                reply="Vale $85.000.", cited_ids=(fixture_id(MOCHILAS, "mochila-azul"),)
            ),
            second.messages[-1]: TurnOutcome(
                reply="El envío cuesta $8.000.", cited_ids=(fixture_id(MOCHILAS, "envio-medellin"),)
            ),
        }
    )
    providers, ledger = replay(tmp_path)

    result = await run_conversations(
        [first, second], agent=agent, providers=providers, ledger=ledger
    )

    assert result.status == "passed"
    assert [score.case_id for score in result.cases] == [first.case_id, second.case_id]
    assert result.metrics.groundedness_rate == 1.0
    assert result.metrics.invented_price_rate == 0.0
    assert agent.turns[0].seller.business_name == "Mochilas Ana"
    assert agent.turns[0].history == ()


async def test_as_7_2_an_invented_price_in_any_reply_fails_the_run(tmp_path: Path) -> None:
    chat = next(case for case in load_cases() if len(case.messages) == 3)
    script = {message: TurnOutcome(reply="¡Con gusto, veci!") for message in chat.messages}
    script[chat.messages[0]] = TurnOutcome(reply="Todo te sale en $1.000.")
    providers, ledger = replay(tmp_path)

    result = await run_conversations(
        [chat], agent=ScriptedAgent(script), providers=providers, ledger=ledger
    )

    assert result.status == "failed"
    assert result.metrics.invented_price_rate == round(1 / 3, 4)
    assert result.cases[0].invented_amounts == (1000,)


async def test_as_7_2_one_invented_price_fails_the_run(tmp_path: Path) -> None:
    first = load_cases()[0]
    agent = ScriptedAgent({first.messages[-1]: TurnOutcome(reply="Vale $80.000.")})
    providers, ledger = replay(tmp_path)

    result = await run_conversations([first], agent=agent, providers=providers, ledger=ledger)

    assert result.status == "failed"
    assert result.metrics.invented_price_rate == 1.0


async def test_earlier_messages_reach_the_agent_as_history(tmp_path: Path) -> None:
    [multi] = [case for case in load_cases() if case.case_id == "c07-saludo-y-bolso"]
    agent = ScriptedAgent(
        {
            multi.messages[0]: TurnOutcome(reply="¡Hola! ¿En qué te ayudo?"),
            multi.messages[1]: TurnOutcome(
                reply="Vale $145.000.", cited_ids=(fixture_id(MOCHILAS, "bolso-cuero"),)
            ),
        }
    )
    providers, ledger = replay(tmp_path)

    result = await run_conversations([multi], agent=agent, providers=providers, ledger=ledger)

    assert result.status == "passed"
    assert [turn.history for turn in agent.turns] == [
        (),
        (Exchange(customer=multi.messages[0], reply="¡Hola! ¿En qué te ayudo?"),),
    ]


async def test_a_case_that_errors_makes_the_run_an_error_not_a_pass(tmp_path: Path) -> None:
    first, second = load_cases()[:2]
    agent = ScriptedAgent(
        {
            first.messages[-1]: TurnOutcome(
                reply="Vale $85.000.", cited_ids=(fixture_id(MOCHILAS, "mochila-azul"),)
            )
        }
    )
    providers, ledger = replay(tmp_path)

    result = await run_conversations(
        [first, second], agent=agent, providers=providers, ledger=ledger
    )

    assert result.status == "error"
    assert not result.cases[1].passed
    assert "no scripted answer" in result.cases[1].notes


async def test_without_an_agent_the_run_is_an_error_that_says_why(tmp_path: Path) -> None:
    providers, ledger = replay(tmp_path)

    result = await run_conversations(load_cases(), agent=None, providers=providers, ledger=ledger)

    assert result.status == "error"
    assert result.cases == []
    assert "run_turn" in result.notes


async def test_as_7_3_the_agent_gets_the_runs_replay_providers(tmp_path: Path) -> None:
    first = load_cases()[0]
    modes: list[ProviderMode] = []

    class ModelAgent:
        async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
            modes.append(turn.providers.mode)
            request = ChatRequest(
                model="m",
                purpose="agent",
                messages=(ChatMessage(role="user", content=turn.message),),
            )
            # No cassette here: the point is which providers the agent received.
            with contextlib.suppress(ProviderError):
                await turn.providers.chat.complete(request)
            return TurnOutcome(
                reply="Vale $85.000.", cited_ids=(fixture_id(MOCHILAS, "mochila-azul"),)
            )

    providers, ledger = replay(tmp_path)

    result = await run_conversations(
        [first], agent=ModelAgent(), providers=providers, ledger=ledger
    )

    assert modes == [ProviderMode.replay]
    assert result.metrics.cost_usd_total == 0.0
