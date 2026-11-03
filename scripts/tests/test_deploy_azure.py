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

from vitrina_core.jobs import TaskRegistry, load_tasks
from vitrina_core.settings import REPO_ROOT, Settings
from vitrina_worker.main import worker_registry

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


CHECK_COMMANDS = [
    "az containerapp show",
    *["az containerapp revision"] * 4,
    "az containerapp replica",
]


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
        *CHECK_COMMANDS,
        "az containerapp create",
        *CHECK_COMMANDS,
    ]
    api, worker, _inbound = (line for line in commands if line.startswith("az containerapp create"))
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
    assert out.count("az containerapp secret set") == 3
    updates = [line for line in out.splitlines() if line.startswith("az containerapp update")]
    assert len(updates) == 3
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


INBOUND_LANE = "conversation.process_inbound,compliance.erase_contact"
CHECK = REPO_ROOT / "deploy" / "check-running.sh"


def env_of(call: list[str], flag: str) -> set[str]:
    """The ``NAME=value`` arguments after ``flag`` in one az call."""
    values: list[str] = []
    for arg in call[call.index(flag) + 1 :]:
        if arg.startswith("--"):
            break
        values.append(arg)
    return set(values)


def test_a_first_deploy_creates_the_inbound_worker_from_the_same_image(tmp_path: Path) -> None:
    created = by_app(dry_run(tmp_path)[0], "create")
    worker, inbound = created["vitrina-worker"], created["vitrina-worker-inbound"]

    line = " ".join(inbound)
    assert "--image vitrinaregistry.azurecr.io/vitrina:test" in line
    assert "--cpu 0.25 --memory 0.5Gi" in line
    assert "--min-replicas 1 --max-replicas 1" in line
    assert "--command vitrina-worker" in line
    assert "--ingress" not in inbound
    assert env_of(inbound, "--secrets") == env_of(worker, "--secrets")
    assert not any("visitor-proxy" in arg for arg in inbound)


def test_the_two_workers_differ_only_in_the_tasks_they_claim(tmp_path: Path) -> None:
    created = by_app(dry_run(tmp_path)[0], "create")
    second = by_app(dry_run(tmp_path, exists=True)[0], "update")

    for calls, flag in ((created, "--env-vars"), (second, "--set-env-vars")):
        worker = env_of(calls["vitrina-worker"], flag)
        inbound = env_of(calls["vitrina-worker-inbound"], flag)
        assert worker - inbound == {f"WORKER_EXCLUDE_TASKS={INBOUND_LANE}"}
        assert inbound - worker == {f"WORKER_TASKS={INBOUND_LANE}"}
        assert not any(arg.startswith("WORKER_") for arg in env_of(calls["vitrina-api"], flag))


def test_the_deployed_limits_name_a_real_task_and_leave_every_task_with_one_worker(
    tmp_path: Path,
) -> None:
    created = by_app(dry_run(tmp_path)[0], "create")
    settings = {
        app: dict(arg.split("=", 1) for arg in env_of(created[app], "--env-vars"))
        for app in ("vitrina-worker", "vitrina-worker-inbound")
    }

    general = worker_registry(
        Settings(  # type: ignore[call-arg]
            _env_file=None, worker_exclude_tasks=settings["vitrina-worker"]["WORKER_EXCLUDE_TASKS"]
        )
    )
    inbound = worker_registry(
        Settings(  # type: ignore[call-arg]
            _env_file=None, worker_tasks=settings["vitrina-worker-inbound"]["WORKER_TASKS"]
        )
    )
    everything = TaskRegistry()
    load_tasks(everything)

    assert inbound.names() == set(INBOUND_LANE.split(","))
    assert not general.names() & inbound.names()
    assert general.names() | inbound.names() == everything.names()
    assert inbound.periodic() == ()
    assert len(general.periodic()) == len(everything.periodic()) > 0


@pytest.mark.parametrize("exists", [False, True])
def test_the_general_worker_stops_claiming_messages_before_the_inbound_worker_starts(
    tmp_path: Path, exists: bool
) -> None:
    out = dry_run(tmp_path, exists=exists)[0]
    verb = "update" if exists else "create"
    names = [
        args[args.index("--name") + 1]
        for args in map(shlex.split, out.splitlines())
        if args[:3] == ["az", "containerapp", verb]
    ]

    assert names == ["vitrina-api", "vitrina-worker", "vitrina-worker-inbound"]


def test_a_config_from_before_the_inbound_worker_still_deploys_it(tmp_path: Path) -> None:
    old = tmp_path / "azure.env"
    old.write_text(
        "".join(
            line
            for line in CONFIG.read_text().splitlines(keepends=True)
            if not line.startswith("INBOUND_WORKER_")
        )
    )
    values = {name: f"fake-{name.lower()}-value" for name in secret_map()} | PLAIN
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{name}={value}\n" for name, value in values.items()))

    result = subprocess.run(
        ["bash", str(SCRIPT), str(old)],
        env={**os.environ, "SECRETS_FILE": str(env_file), "DRY_RUN": "1", "IMAGE_TAG": "test"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--cpu 0.25 --memory 0.5Gi" in " ".join(
        by_app(result.stdout, "create")["vitrina-worker-inbound"]
    )


def check_calls(app: str) -> list[list[str]]:
    """The az calls deploy/check-running.sh makes for ``app``, as a dry run prints them."""
    target = ["--name", app, "--resource-group", "vitrina"]
    revision = ["--revision", "<latest revision>"]

    def tsv(query: str) -> list[str]:
        return ["--query", query, "--output", "tsv"]

    return [
        ["az", "containerapp", "show", *target, *tsv("properties.latestRevisionName")],
        *(
            ["az", "containerapp", "revision", "show", *target, *revision, *tsv(f"properties.{f}")]
            for f in (
                "template.containers[0].image",
                "provisioningState",
                "runningState",
                "replicas",
            )
        ),
        [
            "az",
            "containerapp",
            "replica",
            "list",
            *target,
            *revision,
            *tsv("sum([].properties.containers[].restartCount)"),
        ],
    ]


@pytest.mark.parametrize("exists", [False, True])
def test_a_dry_run_checks_each_worker_right_after_deploying_it_general_first(
    tmp_path: Path, exists: bool
) -> None:
    verb = "update" if exists else "create"
    calls = [
        shlex.split(line)
        for line in dry_run(tmp_path, exists=exists)[0].splitlines()
        if line.startswith("az containerapp ")
    ]
    deploys = [
        number
        for number, call in enumerate(calls)
        if call[:3] == ["az", "containerapp", verb]
        and call[call.index("--name") + 1] in ("vitrina-worker", "vitrina-worker-inbound")
    ]
    general, inbound = deploys

    assert calls[general][calls[general].index("--name") + 1] == "vitrina-worker"
    assert calls[general + 1 : general + 7] == check_calls("vitrina-worker")
    assert calls[inbound][calls[inbound].index("--name") + 1] == "vitrina-worker-inbound"
    assert calls[inbound + 1 : inbound + 7] == check_calls("vitrina-worker-inbound")
    assert inbound > general + 6


def test_a_dry_run_runs_the_api_check_last(tmp_path: Path) -> None:
    lines = dry_run(tmp_path)[0].splitlines()

    assert lines[-1] == "curl -fsS https://<api fqdn>/v1/health"


def stub_az(tmp_path: Path, **answers: list[str]) -> dict[str, str]:
    """An ``az`` that answers each state from a list, repeating the last answer; the environment
    to run with it. ``running.<app>`` answers for that app alone."""
    for name, lines in answers.items():
        (tmp_path / name).write_text("".join(f"{line}\n" for line in lines))
    stub = tmp_path / "bin" / "az"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(
        f"""#!/bin/sh
cd {shlex.quote(str(tmp_path))}
echo "$@" >> az-calls
previous=""
for arg in "$@"; do
  [ "$previous" = --name ] && app=$arg
  previous=$arg
done
next() {{
  file=$1; [ -f "$1.$app" ] && file="$1.$app"
  n=$(cat "$file.n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$file.n"
  line=$(sed -n "${{n}}p" "$file"); [ -n "$line" ] || line=$(tail -n 1 "$file")
  printf '%s\\n' "$line"
}}
case "$*" in
  *properties.latestRevisionName*) echo "$app--r1" ;;
  *"properties.template.containers[0].image"*) next image ;;
  *properties.provisioningState*) next provisioning ;;
  *properties.runningState*) next running ;;
  *properties.replicas*) next replicas ;;
  *restartCount*) next restarts ;;
  *"acr credential show"*) echo registry-value ;;
  *ingress.fqdn*) echo api.example ;;
esac
"""
    )
    stub.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}",
        "CHECK_ATTEMPTS": "6",
        "CHECK_INTERVAL_S": "0",
    }


NEW_IMAGE = "vitrinaregistry.azurecr.io/vitrina:test"
OLD_IMAGE = "vitrinaregistry.azurecr.io/vitrina:older"
HEALTHY = {
    "image": [NEW_IMAGE],
    "provisioning": ["Provisioned"],
    "running": ["Running"],
    "replicas": ["1"],
    "restarts": ["0"],
}


def check_running(tmp_path: Path, **answers: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECK), "vitrina-worker-inbound", "vitrina", NEW_IMAGE],
        env=stub_az(tmp_path, **(HEALTHY | answers)),
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"running": ["Running (at max)"]},
        {"running": ["RunningAtMaxScale"]},
        {"running": ["Processing", "Processing", "Running"]},
        {"replicas": ["0", "1"]},
        {"restarts": ["2"]},
    ],
    ids=["running", "at max", "at max as one word", "starts up", "first replica", "old restarts"],
)
def test_a_worker_that_keeps_running_passes_the_check(
    tmp_path: Path, answers: dict[str, list[str]]
) -> None:
    result = check_running(tmp_path, **answers)

    assert result.returncode == 0, result.stderr
    assert "vitrina-worker-inbound is running" in result.stdout


@pytest.mark.parametrize(
    "answers",
    [
        {"running": ["Degraded"]},
        {"running": ["Stopped"]},
        {"running": ["Unknown"]},
        {"running": [""]},
        {"running": ["Activating"]},
        {"running": ["Scale to 0"]},
        {"replicas": ["0"]},
        {"replicas": [""]},
        {"restarts": [""]},
        {"restarts": ["0", "1", "2", "3", "4", "5"]},
        {"running": ["Running", "Running", "Degraded", "Running", "Running", "Degraded"]},
        {"image": [OLD_IMAGE]},
        {"image": [""]},
    ],
    ids=[
        "degraded",
        "stopped",
        "unknown",
        "blank",
        "activating",
        "scaled to zero",
        "no replica",
        "replicas blank",
        "restarts blank",
        "crash loop",
        "flapping",
        "an old revision still running",
        "image blank",
    ],
)
def test_a_worker_that_is_not_running_fails_the_check_and_names_the_app(
    tmp_path: Path, answers: dict[str, list[str]]
) -> None:
    result = check_running(tmp_path, **answers)

    assert result.returncode == 1
    assert "vitrina-worker-inbound is not running" in result.stderr
    assert "vitrina-worker-inbound is running" not in result.stdout


@pytest.mark.parametrize(
    "answers",
    [{"provisioning": ["Failed"]}, {"running": ["Failed"]}, {"running": ["Activation failed"]}],
    ids=["provisioning", "running", "activation"],
)
def test_a_failed_revision_stops_the_check_at_once(
    tmp_path: Path, answers: dict[str, list[str]]
) -> None:
    result = check_running(tmp_path, **answers)

    assert result.returncode == 1
    assert "vitrina-worker-inbound is not running" in result.stderr
    calls = (tmp_path / "az-calls").read_text().splitlines()
    assert sum("properties.latestRevisionName" in call for call in calls) == 1


def test_the_check_fails_when_az_itself_fails(tmp_path: Path) -> None:
    env = stub_az(tmp_path)
    (tmp_path / "bin" / "az").write_text("#!/bin/sh\nexit 97\n")

    result = subprocess.run(
        ["bash", str(CHECK), "vitrina-worker-inbound", "vitrina", NEW_IMAGE],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "vitrina-worker-inbound is not running" in result.stderr


def real_run_with_stubs(tmp_path: Path, **answers: list[str]) -> subprocess.CompletedProcess[str]:
    """The script's real path, with az, uv and curl all stubs: nothing leaves this machine."""
    env = stub_az(tmp_path, **(HEALTHY | answers))
    for tool in ("uv", "curl"):
        stub = tmp_path / "bin" / tool
        stub.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / (tool + "-calls")}\nexit 0\n')
        stub.chmod(0o755)
    env_file = tmp_path / ".env"
    values = {name: f"fake-{name.lower()}-value" for name in secret_map()} | PLAIN
    env_file.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    return subprocess.run(
        ["bash", str(SCRIPT), str(CONFIG)],
        env={**env, "SECRETS_FILE": str(env_file), "DRY_RUN": "0", "IMAGE_TAG": "test"},
        capture_output=True,
        text=True,
    )


def test_a_deploy_does_not_report_success_while_the_inbound_worker_is_not_running(
    tmp_path: Path,
) -> None:
    result = real_run_with_stubs(tmp_path, **{"running.vitrina-worker-inbound": ["Degraded"]})

    assert result.returncode == 1
    assert "DEPLOY FAILED: vitrina-worker-inbound is not running" in result.stderr
    assert "WhatsApp webhook:" not in result.stdout
    assert not (tmp_path / "curl-calls").exists()


@pytest.mark.parametrize(
    "answers",
    [
        {"running.vitrina-worker": ["Degraded"]},
        {"running.vitrina-worker": ["Failed"]},
        {"provisioning.vitrina-worker": ["Failed"]},
        {"image.vitrina-worker": [OLD_IMAGE]},
    ],
    ids=["degraded", "failed", "provisioning failed", "an old revision still running"],
)
def test_a_general_worker_that_fails_stops_the_deploy_before_the_inbound_worker_is_touched(
    tmp_path: Path, answers: dict[str, list[str]]
) -> None:
    result = real_run_with_stubs(tmp_path, **answers)

    assert result.returncode == 1
    assert "DEPLOY FAILED: vitrina-worker is not running" in result.stderr
    assert "vitrina-worker-inbound" not in (tmp_path / "az-calls").read_text()
    assert "WhatsApp webhook:" not in result.stdout
    assert not (tmp_path / "curl-calls").exists()


def test_a_deploy_goes_on_to_the_api_check_once_both_workers_run(tmp_path: Path) -> None:
    result = real_run_with_stubs(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "vitrina-worker is running" in result.stdout
    assert "vitrina-worker-inbound is running" in result.stdout
    assert "WhatsApp webhook:" in result.stdout


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


def test_fr_064_the_template_account_settings_reach_the_apps(tmp_path: Path) -> None:
    values = {name: f"fake-{name.lower()}-value" for name in secret_map()} | PLAIN
    values |= {
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "100000000000002",
        "WHATSAPP_BUSINESS_NUMBER": "+573000000000",
    }

    result = deploy(tmp_path, values)

    assert result.returncode == 0, result.stderr
    assert "WHATSAPP_BUSINESS_ACCOUNT_ID=100000000000002" in result.stdout
    assert "WHATSAPP_BUSINESS_NUMBER=+573000000000" in result.stdout


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
