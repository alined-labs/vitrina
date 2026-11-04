"""scripts/sync-template-status.py: Meta's status reaches the seller through the webhook route."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.sellers import SellerCreate
from vitrina_core.settings import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "sync-template-status.py"
_spec = importlib.util.spec_from_file_location("sync_template_status", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync
_spec.loader.exec_module(sync)

ACCOUNT = "100000000000002"
NUMBER = "+573000000000"
APP_SECRET = "sync-template-status-app-secret"
TOKEN = "sync-template-status-token"
OURS = {"name": "vitrina_reactivacion", "language": "es", "status": "APPROVED", "id": "1"}


def graph_answering(*pages: dict[str, Any], status: int = 200) -> tuple[httpx2.Client, list[Any]]:
    seen: list[httpx2.Request] = []
    script = list(pages)

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=script.pop(0))

    return (
        httpx2.Client(
            base_url="https://graph.example/v26.0",
            transport=httpx2.MockTransport(answer),
        ),
        seen,
    )


@pytest.fixture
def api() -> tuple[TestClient, dict[str, Any]]:
    app = create_app(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            whatsapp_app_secret=APP_SECRET,
            whatsapp_business_account_id=ACCOUNT,
            whatsapp_business_number=NUMBER,
        )
    )
    seller = asyncio.run(
        app.state.sellers.create(
            SellerCreate(
                business_name="Mochilas Ana", owner_name="Ana", whatsapp_number=NUMBER, city="Cali"
            ),
            auth_subject=None,
        )
    )
    return TestClient(app, base_url="http://testserver/v1"), app.state.sellers.rows[seller.id]


def test_fr_064_an_earlier_approval_reaches_the_seller(api: tuple[TestClient, Any]) -> None:
    client, row = api
    graph, seen = graph_answering(
        {"data": [{**OURS, "language": "en_US", "status": "REJECTED"}, OURS]}
    )

    status = sync.template_status(graph, ACCOUNT)
    sync.deliver(client, sync.event_body(ACCOUNT, status, at=1793114100), app_secret=APP_SECRET)

    assert status == "APPROVED"
    assert seen[0].url.path == f"/v26.0/{ACCOUNT}/message_templates"
    assert row["marketing_template_status"] == "approved"


def test_fr_064_a_template_meta_has_not_approved_does_not_open_the_gate(
    api: tuple[TestClient, Any],
) -> None:
    client, row = api
    graph, _ = graph_answering({"data": [{**OURS, "status": "PENDING"}]})

    status = sync.template_status(graph, ACCOUNT)
    sync.deliver(client, sync.event_body(ACCOUNT, status, at=1793114100), app_secret=APP_SECRET)

    assert row["marketing_template_status"] == "pending"


def test_fr_064_a_wrong_app_secret_changes_nothing(api: tuple[TestClient, Any]) -> None:
    client, row = api

    with pytest.raises(sync.SyncError, match="401"):
        sync.deliver(client, sync.event_body(ACCOUNT, "APPROVED", at=1), app_secret="wrong")

    assert row["marketing_template_status"] == "none"


def test_fr_064_the_template_may_be_on_a_later_page() -> None:
    other = {"name": "hello_world", "language": "en_US", "status": "APPROVED"}
    graph, seen = graph_answering(
        {
            "data": [other],
            "paging": {"cursors": {"after": "NEXT"}, "next": "https://graph.example"},
        },
        {"data": [OURS]},
    )

    assert sync.template_status(graph, ACCOUNT) == "APPROVED"
    assert seen[1].url.params["after"] == "NEXT"


def test_fr_064_an_account_without_the_template_is_reported() -> None:
    graph, _ = graph_answering({"data": [{**OURS, "name": "hello_world"}]})

    with pytest.raises(sync.SyncError, match="no template vitrina_reactivacion/es"):
        sync.template_status(graph, ACCOUNT)


def test_fr_064_a_refusal_from_meta_prints_its_code_and_no_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WHATSAPP_BUSINESS_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    refused = httpx2.Client(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(403, json={"error": {"code": 200}})
        ),
        base_url="https://graph.example",
    )
    monkeypatch.setattr(sync.httpx2, "Client", lambda **_settings: refused)

    assert sync.main(["--api", "https://api.example/v1"]) == 1

    printed = capsys.readouterr()
    assert "Meta answered 403 (code 200)" in printed.err
    assert TOKEN not in printed.err + printed.out
    assert APP_SECRET not in printed.err + printed.out


def test_fr_064_a_missing_setting_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("WHATSAPP_BUSINESS_ACCOUNT_ID", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)

    assert sync.main(["--api", "https://api.example/v1", "--dry-run"]) == 1

    assert "set WHATSAPP_BUSINESS_ACCOUNT_ID" in capsys.readouterr().err


@pytest.mark.parametrize(
    "api",
    [
        "http://api.example/v1",
        "ftp://api.example/v1",
        "api.example/v1",
        "http://localhost.example/v1",
    ],
)
def test_fr_064_the_signed_event_never_leaves_over_plain_http(
    api: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WHATSAPP_BUSINESS_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)

    def no_network(**_settings: Any) -> None:
        raise AssertionError("nothing is called before the address is checked")

    monkeypatch.setattr(sync.httpx2, "Client", no_network)

    assert sync.main(["--api", api]) == 1

    assert "--api must be https" in capsys.readouterr().err


@pytest.mark.parametrize(
    "api",
    [
        "https://api.example/v1",
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]/v1",
    ],
)
def test_fr_064_https_and_this_machine_are_accepted(api: str) -> None:
    sync.check_api(api)
