"""``make evals`` replays from a clean clone, and a developer's own settings cannot break it.

Replay finds a cassette by a hash of the whole request, so the target itself must fix what goes
into that hash: nothing here runs a model, and ``uv`` is replaced by a script that notes the
environment each suite would have started with.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from vitrina_core.settings import REPO_ROOT, Settings
from vitrina_evals import suites

CASSETTES = REPO_ROOT / "evals" / "cassettes"
# A developer's .env that differs in everything that goes into a recorded request.
DIFFERENT_DOTENV = """\
MODEL_AGENT=some-other-model
MODEL_SEGMENT=some-other-segmenter
AGENT_REASONING_EFFORT=high
SCOPE_REASONING_EFFORT=low
RETRIEVAL_TOP_K=2
AGENT_MAX_TOOL_ROUNDS=1
CASSETTE_DIR=/nonexistent
REPLAY=false
RECORD_CASSETTES=true
"""
PINNED = ("MODEL_", "AGENT_", "SCOPE_", "RETRIEVAL_", "CASSETTE_")


def make_evals_environment(tmp_path: Path, **shell: str) -> dict[str, str]:
    seen = tmp_path / "seen.env"
    fake_uv = tmp_path / "fake-uv"
    fake_uv.write_text(f'#!/bin/sh\nenv > "{seen}"\n')
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith(("MAKE", "MFLAGS"))
    }
    subprocess.run(
        ["make", "--no-print-directory", "-C", str(REPO_ROOT), "evals", f"UV={fake_uv}"],
        env={**environment, **shell},
        check=True,
        capture_output=True,
        timeout=30,
    )
    return dict(line.partition("=")[::2] for line in seen.read_text().splitlines())


def recorded_values(kind: str, field: str) -> set[str]:
    values = (
        json.loads(path.read_text(encoding="utf-8"))["request"].get(field)
        for path in (CASSETTES / kind).glob("*.json")
    )
    return {value for value in values if value is not None}


def test_as_7_3_make_evals_sets_the_recorded_models_with_nothing_configured(
    tmp_path: Path,
) -> None:
    seen = make_evals_environment(tmp_path)

    assert seen["MODEL_AGENT"] in recorded_values("chat", "model")
    assert seen["MODEL_SEGMENT"] in recorded_values("segment", "model")


def test_as_7_3_make_evals_keeps_the_recorded_models_over_the_shell(tmp_path: Path) -> None:
    seen = make_evals_environment(tmp_path, MODEL_AGENT="other", MODEL_SEGMENT="other")

    assert seen["MODEL_AGENT"] in recorded_values("chat", "model")
    assert seen["MODEL_SEGMENT"] in recorded_values("segment", "model")


@pytest.mark.parametrize("suite", ["conversations_es", "fidelity"])
async def test_as_7_3_a_different_local_env_file_causes_no_replay_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, suite: str
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(DIFFERENT_DOTENV)
    for name, value in make_evals_environment(tmp_path).items():
        if name.startswith(PINNED):
            monkeypatch.setenv(name, value)
    settings = Settings(_env_file=dotenv)  # type: ignore[call-arg]

    result = await suites.execute(suite, replay=True, settings=settings)  # type: ignore[arg-type]

    assert result.status == "passed", [case.notes for case in result.cases]
    # The scope check survives a miss by logging it, so a passing run can still hide one.
    assert "CassetteMissError" not in caplog.text
