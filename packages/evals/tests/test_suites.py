import json
from pathlib import Path

import pytest

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import CallMeta, ChatMessage, ChatRequest, ChatResponse
from vitrina_core.providers.compose import LiveProviders, ProviderMode, Providers, compose
from vitrina_core.settings import Settings
from vitrina_evals import __main__ as cli
from vitrina_evals import suites
from vitrina_evals.cases import fixture_id, load_cases
from vitrina_evals.runner import AgentTurn, SuiteResult
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import TurnOutcome


def settings(tmp_path: Path, **values: object) -> Settings:
    return Settings(_env_file=None, cassette_dir=tmp_path, **values)  # type: ignore[arg-type]


class TrueAgent:
    """Answers the first case truthfully, asking the chat model once per turn."""

    async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
        request = ChatRequest(
            model="m", purpose="agent", messages=(ChatMessage(role="user", content=turn.message),)
        )
        await turn.providers.chat.complete(request)
        return TurnOutcome(
            reply="La mochila azul vale $85.000.",
            cited_ids=(fixture_id("mochilas_ana", "mochila-azul"),),
        )


class PricedChat:
    async def complete(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content="{}"),
            meta=CallMeta(
                provider="meta_model_api", model=request.model, purpose="agent", cost_usd=0.002
            ),
        )


async def test_fr_070_a_live_run_reports_what_its_calls_cost(tmp_path: Path) -> None:
    live = compose(
        settings(tmp_path),
        InMemoryLedger(),
        lambda: LiveProviders(chat=PricedChat()),
        mode=ProviderMode.live,
    )

    result = await suites.execute(
        "conversations_es",
        replay=False,
        settings=settings(tmp_path),
        live=live,
        sample=1,
        agent=TrueAgent(),
    )

    assert result.status == "passed"
    assert result.metrics.cost_usd_total == 0.002


async def test_as_7_3_a_replay_run_uses_replay_providers_even_in_a_live_process(
    tmp_path: Path,
) -> None:
    live = compose(
        settings(tmp_path),
        InMemoryLedger(),
        lambda: LiveProviders(chat=PricedChat()),
        mode=ProviderMode.live,
    )

    result = await suites.execute(
        "conversations_es",
        replay=True,
        settings=settings(tmp_path),
        live=live,
        sample=1,
        agent=TrueAgent(),
    )

    # The agent's chat call found no cassette: replay never reached the live model.
    assert result.status == "error"
    assert "CassetteMissError" in result.cases[0].notes


async def test_until_the_engine_has_run_turn_a_run_says_so(tmp_path: Path) -> None:
    result = await suites.execute("conversations_es", replay=True, settings=settings(tmp_path))

    assert result.status == "error"
    assert "run_turn" in result.notes


def test_make_evals_reports_an_error_run_without_failing_the_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not the machine's .env: without MODEL_AGENT there is no agent, so the run is an error.
    monkeypatch.setattr(cli, "get_settings", lambda: settings(tmp_path))

    code = cli.main(["--suite", "conversations_es", "--sample", "2", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    report = json.loads(out[: out.rindex("}") + 1])
    assert code == 0
    assert report["status"] == "error"
    assert "::warning title=evals conversations_es::" in out
    [written] = tmp_path.glob("conversations_es-*.json")
    assert json.loads(written.read_text())["status"] == "error"


def test_as_7_2_an_invented_price_fails_make_evals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Inventor:
        async def run_turn(self, turn: AgentTurn) -> TurnOutcome:
            return TurnOutcome(reply="Te la dejo en $50.000.")

    monkeypatch.setattr(suites, "conversation_agent", lambda _settings: Inventor())

    code = cli.main(["--suite", "conversations_es", "--sample", "1"])

    assert code == 1
    assert '"status": "failed"' in capsys.readouterr().out


def test_the_cases_the_cli_runs_are_the_fixture_file() -> None:
    assert len(load_cases()) == 60


def test_t055_record_calls_the_live_providers_and_stores_their_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def execute(suite: str, *, replay: bool, live: Providers | None, **_: object) -> object:
        seen.update(replay=replay, mode=live.mode if live else None)
        return SuiteResult("error", EvalMetrics(), [], "stopped by the test")

    monkeypatch.setattr(cli, "get_settings", lambda: settings(tmp_path, meta_api_key="test-key"))
    monkeypatch.setattr(cli, "execute", execute)

    cli.main(["--suite", "conversations_es", "--record"])

    assert seen == {"replay": False, "mode": ProviderMode.record}


def test_t055_case_runs_only_the_named_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def execute(suite: str, **options: object) -> object:
        seen.update(options)
        return SuiteResult("error", EvalMetrics(), [], "stopped by the test")

    monkeypatch.setattr(cli, "get_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(cli, "execute", execute)

    cli.main(["--case", "c10-regateo-60-mil", "--case", "c12-precio-de-instagram"])

    assert seen["cases"] == ["c10-regateo-60-mil", "c12-precio-de-instagram"]


def test_load_cases_keeps_the_named_cases_in_file_order() -> None:
    ids = ["c12-precio-de-instagram", "c10-regateo-60-mil"]

    assert [case.case_id for case in load_cases(only=ids)] == sorted(ids)
