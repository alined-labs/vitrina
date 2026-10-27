from pathlib import Path

import pytest

from vitrina_core.settings import Settings
from vitrina_evals import __main__ as cli
from vitrina_evals import suites
from vitrina_evals.consent import load_consent_cases, run_consent_scheduler


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, cassette_dir=tmp_path)  # type: ignore[call-arg]


def test_t065_the_consent_scheduler_suite_passes_all_forty_cases() -> None:
    result = run_consent_scheduler(load_consent_cases())

    assert result.status == "passed"
    assert result.metrics.consent_scheduler_accuracy == 1.0
    assert len(result.cases) == 40
    assert all(case.passed for case in result.cases)


def test_t065_one_wrong_decision_fails_the_suite() -> None:
    cases = load_consent_cases()
    wrong = {**cases[0], "expect": {"decision": "blocked", "reason": "FREQUENCY_CAP"}}

    result = run_consent_scheduler([wrong, *cases[1:]])

    assert result.status == "failed"
    assert result.metrics.consent_scheduler_accuracy == round(39 / 40, 4)
    assert not result.cases[0].passed
    assert "expected blocked/FREQUENCY_CAP" in result.cases[0].notes


async def test_the_suite_runs_through_execute(tmp_path: Path) -> None:
    result = await suites.execute("consent_scheduler", replay=True, settings=settings(tmp_path))

    assert result.status == "passed"


def test_t065_make_evals_passes_on_the_consent_table(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["--suite", "consent_scheduler"])

    assert code == 0
    assert '"consent_scheduler_accuracy": 1.0' in capsys.readouterr().out
