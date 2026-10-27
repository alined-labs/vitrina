"""``Idempotency-Key`` on mutating POSTs (FR-081; data-model.md ``idempotency_keys``).

Same key and same request: the stored response is replayed and the handler does not run
again. Same key, different request: ``409 IDEMPOTENCY_CONFLICT``. Keys live 24 hours, are
scoped per seller, and a 5xx or 429 is never stored, so the client can retry.
"""

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from fastapi import Header
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vitrina_core.db import metadata
from vitrina_core.http.problems import FieldError, ProblemCode, ProblemError, problem_response
from vitrina_core.ids import NIL

TTL = timedelta(hours=24)
MIN_KEY, MAX_KEY = 8, 128

# Declare on each mutating route so the header appears in the OpenAPI document; the
# middleware does the work.
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=MIN_KEY, max_length=MAX_KEY)
]

idempotency_keys = sa.Table(
    "idempotency_keys",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("seller_id", sa.Uuid, primary_key=True),
    sa.Column("request_hash", sa.Text, nullable=False),
    sa.Column("response_status", sa.Integer),
    sa.Column("response_body", JSONB),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)


@dataclass(frozen=True)
class Outcome:
    kind: Literal["new", "replay", "conflict", "in_progress"]
    status: int | None = None
    body: Any = None


class IdempotencyStore(Protocol):
    async def begin(self, scope: UUID, key: str, request_hash: str) -> Outcome: ...

    async def finish(self, scope: UUID, key: str, status: int, body: Any) -> None: ...

    async def abandon(self, scope: UUID, key: str) -> None: ...


@dataclass
class _Entry:
    request_hash: str
    created_at: datetime
    status: int | None = None
    body: Any = None


@dataclass
class InMemoryIdempotencyStore:
    """For tests and machines without a database; not shared across processes."""

    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _entries: dict[tuple[UUID, str], _Entry] = field(default_factory=dict)

    async def begin(self, scope: UUID, key: str, request_hash: str) -> Outcome:
        now = self.clock()
        entry = self._entries.get((scope, key))
        if entry is None or now - entry.created_at > TTL:
            self._entries[(scope, key)] = _Entry(request_hash, now)
            return Outcome("new")
        return _decide(entry.request_hash, request_hash, entry.status, entry.body)

    async def finish(self, scope: UUID, key: str, status: int, body: Any) -> None:
        entry = self._entries[(scope, key)]
        entry.status, entry.body = status, body

    async def abandon(self, scope: UUID, key: str) -> None:
        self._entries.pop((scope, key), None)


class PostgresIdempotencyStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def begin(self, scope: UUID, key: str, request_hash: str) -> Outcome:
        table = idempotency_keys
        where = (table.c.seller_id == scope) & (table.c.key == key)
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.delete(table).where(where, table.c.created_at < sa.func.now() - TTL)
            )
            claimed = await session.execute(
                insert(table)
                .values(seller_id=scope, key=key, request_hash=request_hash)
                .on_conflict_do_nothing()
                .returning(table.c.key)
            )
            if claimed.first() is not None:
                return Outcome("new")
            row = (await session.execute(sa.select(table).where(where))).mappings().one()
        return _decide(
            row["request_hash"], request_hash, row["response_status"], row["response_body"]
        )

    async def finish(self, scope: UUID, key: str, status: int, body: Any) -> None:
        table = idempotency_keys
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(table)
                .where(table.c.seller_id == scope, table.c.key == key)
                .values(response_status=status, response_body=body)
            )

    async def abandon(self, scope: UUID, key: str) -> None:
        table = idempotency_keys
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.delete(table).where(table.c.seller_id == scope, table.c.key == key)
            )


def _decide(stored_hash: str, request_hash: str, status: int | None, body: Any) -> Outcome:
    if stored_hash != request_hash:
        return Outcome("conflict")
    if status is None:
        return Outcome("in_progress")
    return Outcome("replay", status, body)


ScopeResolver = Callable[[Request], UUID | Awaitable[UUID]]


def _anonymous(_request: Request) -> UUID:
    return NIL


class IdempotencyMiddleware:
    """``scope`` maps a request to the seller its keys belong to (NIL when anonymous)."""

    def __init__(
        self, app: ASGIApp, *, store: IdempotencyStore, scope: ScopeResolver = _anonymous
    ) -> None:
        self.app = app
        self.store = store
        self.scope = scope

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        key = Headers(scope=scope).get("idempotency-key") if scope["type"] == "http" else None
        if key is None or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        if not MIN_KEY <= len(key) <= MAX_KEY:
            error = ProblemError(
                ProblemCode.VALIDATION_ERROR,
                errors=[
                    FieldError(
                        field="Idempotency-Key", message=f"must be {MIN_KEY}–{MAX_KEY} characters"
                    )
                ],
            )
            await problem_response(error)(scope, receive, send)
            return

        body = await _read_body(receive)
        principal = self.scope(Request(scope))
        owner = await principal if inspect.isawaitable(principal) else principal
        digest = hashlib.sha256(
            b"\n".join(
                [
                    scope["method"].encode(),
                    scope["path"].encode(),
                    scope.get("query_string", b""),
                    body,
                ]
            )
        )
        outcome = await self.store.begin(owner, key, digest.hexdigest())

        if outcome.kind == "replay":
            replayed = JSONResponse(outcome.body, status_code=outcome.status or 200)
            replayed.headers["Idempotent-Replayed"] = "true"
            await replayed(scope, receive, send)
            return
        if outcome.kind != "new":
            code = (
                ProblemCode.IDEMPOTENCY_CONFLICT
                if outcome.kind == "conflict"
                else ProblemCode.CONFLICT
            )
            detail = (
                None
                if outcome.kind == "conflict"
                else "a request with this Idempotency-Key is still running"
            )
            await problem_response(ProblemError(code, detail=detail))(scope, receive, send)
            return

        captured: dict[str, Any] = {"status": 500, "json": False, "chunks": []}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                content_type = Headers(raw=message.get("headers", [])).get("content-type", "")
                captured["json"] = content_type.split(";")[0].strip().endswith("json")
            elif message["type"] == "http.response.body":
                captured["chunks"].append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, _replay(body), capture)
        except Exception:
            await self.store.abandon(owner, key)
            raise
        status = captured["status"]
        if status >= 500 or status == 429 or not captured["json"]:
            await self.store.abandon(owner, key)
            return
        await self.store.finish(
            owner, key, status, json.loads(b"".join(captured["chunks"]) or b"null")
        )


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive
