"""Foto → Vitrina's upload check in the route, without a database (AS-8.2, FR-072).

The rate limit counts the attempt first; then Spark answers one yes/no question about the
photo. A refused photo gets a short Spanish message and is never stored, and no job is queued.
Over the daily cap or the spend limit, the question is never asked: nothing is spent.
"""

import dataclasses
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_api.routers import public
from vitrina_core.ledger import LedgerEntry
from vitrina_core.providers import ProviderError
from vitrina_core.settings import Settings
from vitrina_creative.testing import ScriptedAnswers, source_photo


class FirstHit:
    """The address's first upload this hour."""

    def __init__(self) -> None:
        self.hits = 0
        self.digests: list[str] = []

    async def hit(self, digest: str, window: datetime) -> int:
        self.hits += 1
        self.digests.append(digest)
        return self.hits


class NoQueue:
    async def enqueue(self, *args: object, **kwargs: object) -> UUID:
        raise AssertionError("a refused upload must not queue a job")


AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppFactory:
    def make(**limits: Any) -> FastAPI:
        settings = Settings(
            _env_file=None, storage_dir=tmp_path, model_agent="spark-test", **limits
        )  # type: ignore[call-arg]
        application = create_app(settings)
        store = FirstHit()
        monkeypatch.setattr(public, "_store", lambda request: (store, NoQueue()))
        application.state.rate_limit = store
        return application

    return make


@pytest.fixture
def app(make_app: AppFactory) -> FastAPI:
    return make_app()


def answering(app: FastAPI, *answers: str | Exception) -> ScriptedAnswers:
    chat = ScriptedAnswers(*answers)
    app.state.providers = dataclasses.replace(app.state.providers, chat=chat)
    return chat


def upload(app: FastAPI, headers: dict[str, str] | None = None) -> Any:
    return TestClient(app).post(
        "/v1/public/showcases",
        files={"image": ("bolso.png", source_photo().image.data, "image/png")},
        data={"product_name": "Bolso tejido a mano"},
        headers=headers,
    )


def stored(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and "showcases" in path.parts]


def test_a_refused_photo_gets_a_short_spanish_message_and_is_never_stored(
    app: FastAPI, tmp_path: Path
) -> None:
    chat = answering(app, json.dumps({"allowed": False}))

    response = upload(app)

    assert response.status_code == 400, response.text
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert (
        problem["detail"] == "Esta foto no se puede usar. Sube una foto de un producto que vendas."
    )
    assert [error["field"] for error in problem["errors"]] == ["image"]
    assert [sent.purpose for sent in chat.requests] == ["classify"]
    assert app.state.rate_limit.hits == 1  # the refused attempt still counts toward five an hour
    assert stored(tmp_path) == []


def test_a_check_that_cannot_run_asks_to_try_again_and_stores_nothing(
    app: FastAPI, tmp_path: Path
) -> None:
    answering(app, ProviderError("rate limited", provider="meta_model_api", retryable=False))

    response = upload(app)

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
    assert (
        response.json()["detail"] == "No pudimos revisar tu foto. Intenta de nuevo en unos minutos."
    )
    assert stored(tmp_path) == []


PROXY_SECRET = "proxy-secret-for-tests"


def visitor(address: str) -> dict[str, str]:
    return {"X-Vitrina-Proxy-Secret": PROXY_SECRET, "X-Vitrina-Visitor": address}


def test_as_8_2_addresses_of_one_ipv6_network_count_as_one_visitor(make_app: AppFactory) -> None:
    app = make_app(visitor_proxy_secret=PROXY_SECRET)
    answering(app, *[json.dumps({"allowed": False})] * 4)

    upload(app, visitor("2001:db8:1:2::a"))
    upload(app, visitor("2001:db8:1:2:ffff::b"))
    upload(app, visitor("2001:db8:1:3::a"))
    upload(app, visitor("203.0.113.7"))

    first, same_network, next_network, ipv4 = app.state.rate_limit.digests
    assert first == same_network
    assert len({first, next_network, ipv4}) == 3


def test_as_8_2_over_the_daily_cap_nothing_is_spent(make_app: AppFactory, tmp_path: Path) -> None:
    app = make_app(public_showcase_daily_cap=1)
    chat = answering(app, json.dumps({"allowed": False}))

    assert upload(app).status_code == 400  # its check was paid for, so it counts
    over = upload(app)

    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"
    assert over.json()["detail"] == (
        "Hoy ya se crearon todas las vitrinas gratis disponibles. Vuelve a intentarlo mañana."
    )
    assert 0 < int(over.headers["Retry-After"]) <= 86400
    assert len(chat.requests) == 1
    assert stored(tmp_path) == []


def test_as_8_2_a_cap_of_zero_closes_the_tool(make_app: AppFactory) -> None:
    app = make_app(public_showcase_daily_cap=0)
    chat = answering(app)

    assert upload(app).status_code == 429
    assert chat.requests == []


def test_as_8_2_an_unreadable_file_costs_nothing_so_it_takes_no_place_under_the_cap(
    make_app: AppFactory,
) -> None:
    app = make_app(public_showcase_daily_cap=1)
    chat = answering(app, json.dumps({"allowed": False}))

    unreadable = TestClient(app).post(
        "/v1/public/showcases",
        files={"image": ("bolso.png", b"not a photo", "image/png")},
        data={"product_name": "Bolso tejido a mano"},
    )

    assert unreadable.status_code == 400
    assert chat.requests == []
    assert upload(app).status_code == 400  # the place is still free: the check runs
    assert len(chat.requests) == 1


def test_as_8_2_a_check_that_could_not_run_gives_its_place_back(make_app: AppFactory) -> None:
    app = make_app(public_showcase_daily_cap=1)
    down = ProviderError("down", provider="meta_model_api", retryable=False)
    chat = answering(app, down, json.dumps({"allowed": False}))

    assert upload(app).status_code == 503
    assert upload(app).status_code == 400
    assert len(chat.requests) == 2


def spent(cost: float, *, replayed: bool = False) -> LedgerEntry:
    return LedgerEntry(
        provider="meta_model_api",
        model="spark-test",
        purpose="scene",
        tokens_in=0,
        tokens_out=0,
        latency_ms=1,
        cost_usd=cost,
        replayed=replayed,
        seller_id=None,  # a showcase's calls belong to no seller
        request_id=None,
    )


async def test_as_8_2_at_the_spend_limit_nothing_more_is_spent_on_the_public_tool(
    make_app: AppFactory, tmp_path: Path
) -> None:
    app = make_app(spend_limit_usd=2.0)
    chat = answering(app)
    await app.state.ledger.record(spent(2.0))

    over = upload(app)

    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"
    assert over.json()["detail"] == (
        "Por ahora no podemos crear más vitrinas gratis. Vuelve a intentarlo más tarde."
    )
    assert int(over.headers["Retry-After"]) > 0
    assert chat.requests == []
    assert stored(tmp_path) == []


async def test_as_8_2_below_the_limit_and_with_replayed_calls_the_tool_stays_open(
    make_app: AppFactory,
) -> None:
    app = make_app(spend_limit_usd=2.0)
    chat = answering(app, json.dumps({"allowed": False}))
    await app.state.ledger.record(spent(1.99))
    await app.state.ledger.record(spent(50.0, replayed=True))

    assert upload(app).status_code == 400  # reached the photo check
    assert len(chat.requests) == 1


async def test_as_8_2_a_stopped_tool_uses_up_no_place_under_the_cap(make_app: AppFactory) -> None:
    app = make_app(spend_limit_usd=1.0, public_showcase_daily_cap=1)
    answering(app, json.dumps({"allowed": False}))
    await app.state.ledger.record(spent(1.0))
    assert upload(app).status_code == 429

    app.state.settings.spend_limit_usd = 5.0  # the limit is raised

    assert upload(app).status_code == 400
