"""Request body limits: refused by declared length, or cut off while a body without one arrives."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request, UploadFile
from fastapi.testclient import TestClient

from vitrina_core.http import (
    BodyLimitMiddleware,
    IdempotencyKey,
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
    RequestIdMiddleware,
    install_problem_handlers,
)
from vitrina_core.ids import NIL

LIMIT = 1_000
UPLOAD_LIMIT = 5_000


def make_client() -> tuple[TestClient, list[str]]:
    calls: list[str] = []
    app = FastAPI()
    install_problem_handlers(app)

    def scope(_request: Request) -> UUID:
        return NIL

    app.add_middleware(IdempotencyMiddleware, store=InMemoryIdempotencyStore(), scope=scope)
    app.add_middleware(BodyLimitMiddleware, limits={"/uploads": UPLOAD_LIMIT}, default=LIMIT)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/notes")
    def note(body: dict[str, Any], _key: IdempotencyKey = None) -> dict[str, int]:
        calls.append("notes")
        return {"size": len(body["text"])}

    @app.post("/uploads")
    async def upload(file: UploadFile) -> dict[str, int]:
        calls.append("uploads")
        return {"size": len(await file.read())}

    return TestClient(app, raise_server_exceptions=False), calls


def note_of(size: int) -> bytes:
    """A JSON body of exactly ``size`` bytes."""
    envelope = b'{"text": ""}'
    return envelope[:-2] + b"x" * (size - len(envelope)) + envelope[-2:]


def chunks(body: bytes, size: int = 256) -> Iterator[bytes]:
    """The body as a stream: sent chunked, with no Content-Length."""
    for start in range(0, len(body), size):
        yield body[start : start + size]


def assert_too_large(response: Any) -> None:
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["x-request-id"]


def test_a_body_at_the_limit_goes_through() -> None:
    client, calls = make_client()
    response = client.post(
        "/notes", content=note_of(LIMIT), headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    assert calls == ["notes"]


def test_a_declared_length_over_the_limit_is_refused_before_the_route_runs() -> None:
    client, calls = make_client()
    response = client.post(
        "/notes", content=note_of(LIMIT + 1), headers={"content-type": "application/json"}
    )
    assert_too_large(response)
    assert calls == []


def test_a_body_without_a_length_is_cut_off_at_the_limit() -> None:
    client, calls = make_client()
    response = client.post(
        "/notes",
        content=chunks(note_of(LIMIT * 4)),
        headers={"content-type": "application/json"},
    )
    assert "content-length" not in response.request.headers
    assert_too_large(response)
    assert calls == []


def test_the_idempotency_middleware_never_buffers_more_than_the_limit() -> None:
    client, calls = make_client()
    response = client.post(
        "/notes",
        content=chunks(note_of(LIMIT * 4)),
        headers={"content-type": "application/json", "idempotency-key": "k" * 20},
    )
    assert_too_large(response)
    assert calls == []


def test_a_route_with_its_own_limit_takes_a_larger_body() -> None:
    client, calls = make_client()
    accepted = client.post("/uploads", files={"file": ("a.bin", b"x" * (LIMIT * 3))})
    refused = client.post("/uploads", files={"file": ("a.bin", b"x" * UPLOAD_LIMIT)})
    assert accepted.status_code == 200
    assert accepted.json() == {"size": LIMIT * 3}
    assert_too_large(refused)
    assert calls == ["uploads"]
