"""Foto → Vitrina's upload check in the route, without a database (AS-8.2, FR-072).

The rate limit counts the attempt first; then Spark answers one yes/no question about the
photo. A refused photo gets a short Spanish message and is never stored, and no job is queued.
"""

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_api.routers import public
from vitrina_core.providers import ProviderError
from vitrina_core.settings import Settings
from vitrina_creative.testing import ScriptedAnswers, source_photo


class FirstHit:
    """The address's first upload this hour."""

    def __init__(self) -> None:
        self.hits = 0

    async def hit(self, digest: str, window: datetime) -> int:
        self.hits += 1
        return self.hits


class NoQueue:
    async def enqueue(self, *args: object, **kwargs: object) -> UUID:
        raise AssertionError("a refused upload must not queue a job")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    settings = Settings(_env_file=None, storage_dir=tmp_path, model_agent="spark-test")  # type: ignore[call-arg]
    application = create_app(settings)
    store = FirstHit()
    monkeypatch.setattr(public, "_store", lambda request: (store, NoQueue()))
    application.state.rate_limit = store
    return application


def answering(app: FastAPI, *answers: str | Exception) -> ScriptedAnswers:
    chat = ScriptedAnswers(*answers)
    app.state.providers = dataclasses.replace(app.state.providers, chat=chat)
    return chat


def upload(app: FastAPI) -> Any:
    return TestClient(app).post(
        "/v1/public/showcases",
        files={"image": ("bolso.png", source_photo().image.data, "image/png")},
        data={"product_name": "Bolso tejido a mano"},
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
