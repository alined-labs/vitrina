"""deploy/azure.sh in dry-run mode (the az commands it would run, with no secret in sight) and
the client address its settings let the API see behind the ingress."""

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr
from starlette.types import Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

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


def deploy_settings() -> dict[str, str]:
    """The SETTINGS both apps run with, as bash reads them from the example config."""
    out = subprocess.run(
        ["bash", "-c", 'source "$1" && printf "%s\\n" "${SETTINGS[@]}"', "bash", str(CONFIG)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


async def client_seen(peer: str, forwarded_for: str) -> str:
    """The client address the API sees, through uvicorn's own proxy-header middleware."""
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["client"][0])

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        return None

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=deploy_settings()["FORWARDED_ALLOW_IPS"])
    scope = {
        "type": "http",
        "client": (peer, 50000),
        "headers": [(b"x-forwarded-for", forwarded_for.encode())],
    }
    await middleware(scope, receive, send)  # type: ignore[arg-type]
    return seen[0]


# What production needs besides the secrets: plain values, not secrets, so not "fake-".
PLAIN = {
    "SUPABASE_URL": "https://example.supabase.co",
    "MODEL_AGENT": "muse-spark-1.3",
    "MODEL_STRATEGIST": "muse-wonder-1.0",
    "MODEL_IMAGE": "muse-image-1.0",
    "MODEL_SEGMENT": "sam-3.1",
    "MODEL_TRANSCRIBE": "muse-voice-transcribe-1.0",
    "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
}


def deploy(
    tmp_path: Path, values: dict[str, str], *, dry: bool = True, exists: bool = False
) -> subprocess.CompletedProcess[str]:
    """The script with ``values`` as its .env; ``az`` is a stub that records any call."""
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    stub = tmp_path / "bin" / "az"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / "az-calls"}\nexit 97\n')
    stub.chmod(0o755)
    return subprocess.run(
        ["bash", str(SCRIPT), str(CONFIG)],
        env={
            **os.environ,
            "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}",
            "SECRETS_FILE": str(env_file),
            "DRY_RUN": "1" if dry else "0",
            "DRY_RUN_EXISTS": "1" if exists else "0",
            "IMAGE_TAG": "test",
        },
        capture_output=True,
        text=True,
    )


def dry_run(tmp_path: Path, *, exists: bool = False) -> tuple[str, dict[str, str]]:
    values = {name: f"fake-{name.lower()}-value" for name in secret_map()} | PLAIN
    result = deploy(tmp_path, values, exists=exists)
    assert result.returncode == 0, result.stderr
    assert "missing" not in result.stderr
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
    forwarded = f"FORWARDED_ALLOW_IPS={deploy_settings()['FORWARDED_ALLOW_IPS']}"
    assert forwarded in shlex.split(api)
    assert forwarded in shlex.split(worker)
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


def by_app(out: str, command: str) -> dict[str, list[str]]:
    """The dry run's `az containerapp <command>` calls, as arguments, by the app they name."""
    calls = {}
    for line in out.splitlines():
        if line.startswith(f"az containerapp {command} "):
            args = shlex.split(line)
            calls[args[args.index("--name") + 1]] = args
    return calls


def test_as_8_2_only_the_api_gets_the_visitor_proxy_secret(tmp_path: Path) -> None:
    created = by_app(dry_run(tmp_path)[0], "create")
    second = dry_run(tmp_path, exists=True)[0]

    assert {"VISITOR_PROXY_SECRET=secretref:visitor-proxy", "visitor-proxy=***"} <= set(
        created["vitrina-api"]
    )
    assert not any("visitor-proxy" in arg for arg in created["vitrina-worker"])
    for command in ("secret set", "update"):
        calls = by_app(second, command)
        assert any("visitor-proxy" in arg for arg in calls["vitrina-api"])
        assert not any("visitor-proxy" in arg for arg in calls["vitrina-worker"])


# The Container Apps ingress appends the caller's address to any X-Forwarded-For the caller
# sent, so the rightmost entry is the only one Azure vouches for. The ingress's own address
# is not documented: one peer per trusted range.
INGRESS_PEERS = ["127.0.0.1", "10.240.0.7", "100.100.0.5", "172.16.3.4", "192.168.1.9"]


@pytest.mark.parametrize("peer", INGRESS_PEERS)
async def test_as_8_2_the_visitor_is_the_address_the_ingress_appended(peer: str) -> None:
    assert await client_seen(peer, "203.0.113.9") == "203.0.113.9"
    assert await client_seen(peer, "192.0.2.1, 203.0.113.9") == "203.0.113.9"
    assert await client_seen(peer, "10.0.0.1, 203.0.113.9") == "203.0.113.9"


async def test_as_8_2_a_peer_outside_the_trusted_ranges_cannot_forward_an_address() -> None:
    assert await client_seen("198.51.100.7", "192.0.2.1") == "198.51.100.7"


@pytest.mark.parametrize(
    "absent", ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "VISITOR_PROXY_SECRET", "MODEL_AGENT"]
)
def test_a_deploy_without_a_production_value_stops_before_any_az_command(
    tmp_path: Path, absent: str
) -> None:
    values = {name: f"fake-{name.lower()}-value" for name in secret_map()} | PLAIN
    del values[absent]

    real = deploy(tmp_path, values, dry=False)
    dry = deploy(tmp_path, values)

    assert real.returncode == 2
    assert absent in real.stderr.split("missing in", 1)[1]
    assert not (tmp_path / "az-calls").exists()
    assert "fake-" not in real.stderr
    # A dry run lists the same names and still prints every command.
    assert dry.returncode == 0
    assert absent in dry.stderr
    assert "az containerapp create" in dry.stdout


def test_optional_values_may_be_left_out(tmp_path: Path) -> None:
    optional = {"SUPABASE_JWT_SECRET", "BASETEN_API_KEY"}
    values = {name: f"fake-{name.lower()}-value" for name in secret_map() if name not in optional}

    result = deploy(tmp_path, values | PLAIN)

    assert result.returncode == 0
    assert "missing" not in result.stderr


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
