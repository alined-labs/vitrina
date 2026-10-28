"""deploy/azure.sh in dry-run mode: the az commands it would run, with no secret in sight."""

import os
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from vitrina_core.settings import REPO_ROOT, Settings

SCRIPT = REPO_ROOT / "deploy" / "azure.sh"
CONFIG = REPO_ROOT / "deploy" / "azure.env.example"


def secret_map() -> dict[str, str]:
    block = SCRIPT.read_text().split("declare -A SECRET_OF=(", 1)[1].split(")", 1)[0]
    return dict(re.findall(r"\[([A-Z_]+)\]=([a-z0-9-]+)", block))


def secret_settings() -> set[str]:
    return {
        name.upper()
        for name, field in Settings.model_fields.items()
        if SecretStr in getattr(field.annotation, "__args__", (field.annotation,))
    }


def dry_run(tmp_path: Path, *, exists: bool = False) -> tuple[str, dict[str, str]]:
    values = {name: f"fake-{name.lower()}-value" for name in secret_map()}
    values["MODEL_AGENT"] = "muse-spark-1.3"
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    result = subprocess.run(
        ["bash", str(SCRIPT), str(CONFIG)],
        env={
            **os.environ,
            "SECRETS_FILE": str(env_file),
            "DRY_RUN": "1",
            "DRY_RUN_EXISTS": "1" if exists else "0",
            "IMAGE_TAG": "test",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout, values


def test_every_secret_setting_is_deployed_as_a_container_apps_secret() -> None:
    mapping = secret_map()

    assert set(mapping) == secret_settings()
    assert all(
        len(name) <= 20 and re.fullmatch(r"[a-z][a-z0-9-]*", name) for name in mapping.values()
    )


def test_a_first_deploy_creates_everything_and_never_prints_a_secret(tmp_path: Path) -> None:
    out, values = dry_run(tmp_path)

    commands = [line for line in out.splitlines() if line.startswith(("az ", "DB_SCHEMA="))]
    assert [" ".join(line.split()[:3]) for line in commands] == [
        "az group create",
        "az acr create",
        "az acr build",
        "DB_SCHEMA=prod uv run",
        "az containerapp env",
        "az containerapp create",
        "az containerapp create",
    ]
    api, worker = (line for line in commands if line.startswith("az containerapp create"))
    assert "--ingress external --target-port 8080" in api
    assert "--cpu 0.25 --memory 0.5Gi" in api
    assert "--ingress" not in worker
    assert "--command vitrina-worker" in worker
    assert "DATABASE_URL=secretref:database-url" in api
    assert "MODEL_AGENT=muse-spark-1.3" in api
    assert "FORWARDED_ALLOW_IPS=\\*" in api
    for value in values.values():
        if value.startswith("fake-"):
            assert value not in out


def test_a_second_deploy_updates_the_apps_to_the_new_image(tmp_path: Path) -> None:
    out, values = dry_run(tmp_path, exists=True)

    assert "az acr create" not in out
    assert "az containerapp create" not in out
    assert out.count("az containerapp secret set") == 2
    updates = [line for line in out.splitlines() if line.startswith("az containerapp update")]
    assert len(updates) == 2
    assert all("--image vitrinaregistry.azurecr.io/vitrina:test" in line for line in updates)
    assert all("--set-env-vars" in line for line in updates)
    assert not any(value in out for value in values.values() if value.startswith("fake-"))


def test_without_its_config_the_script_says_what_is_missing(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "azure.env")],
        env={**os.environ, "DRY_RUN": "1"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "copy deploy/azure.env.example" in result.stderr


@pytest.mark.parametrize("path", [SCRIPT, CONFIG])
def test_the_deploy_files_parse(path: Path) -> None:
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0
