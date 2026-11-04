"""``Idempotency-Key`` on mutating POSTs (FR-081; data-model.md ``idempotency_keys``).

Same key and same request: the stored response is replayed and the handler does not run
again. Same key, different request: ``409 IDEMPOTENCY_CONFLICT``. Keys live 24 hours, are
scoped per seller, and a 5xx or 429 is never stored, so the client can retry.

The caller is resolved from the headers before the body is read. A request with no caller is
passed on as it came: its body is not held in memory, and it neither reads nor writes a stored
response. Such a key has no effect, on an open route too; a stored response could otherwise be
read by anyone holding the key and the body.

A request still running keeps renewing its row. A row nobody renewed for ``stale_after`` lost
its process, so the same request may claim it again; the claim is atomic, so one retry wins.
"""

import asyncio
import hashlib
import inspect
import json
import logging
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

logger = logging.getLogger("vitrina.http")

TTL = timedelta(hours=24)
MIN_KEY, MAX_KEY = 8, 128
# Settings.idempotency_stale_after_s, and renewals six times within it, as the job queue does.
STALE_AFTER = timedelta(seconds=90)
RENEW_EVERY_S = STALE_AFTER.total_seconds() / 6

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

    async def renew(self, scope: UUID, key: str) -> None: ...

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
    stale_after: timedelta = STALE_AFTER
    _entries: dict[tuple[UUID, str], _Entry] = field(default_factory=dict)

    async def begin(self, scope: UUID, key: str, request_hash: str) -> Outcome:
        now = self.clock()
        entry = self._entries.get((scope, key))
        if entry is None or now - entry.created_at > TTL:
            self._entries[(scope, key)] = _Entry(request_hash, now)
            return Outcome("new")
        if (
            entry.status is None
            and entry.request_hash == request_hash
            and now - entry.created_at > self.stale_after
        ):
            entry.created_at = now
            return Outcome("new")
        return _decide(entry.request_hash, request_hash, entry.status, entry.body)

    async def renew(self, scope: UUID, key: str) -> None:
        entry = self._entries.get((scope, key))
        if entry is not None and entry.status is None:
            entry.created_at = self.clock()

    async def finish(self, scope: UUID, key: str, status: int, body: Any) -> None:
        entry = self._entries[(scope, key)]
        entry.status, entry.body = status, body

    async def abandon(self, scope: UUID, key: str) -> None:
        self._entries.pop((scope, key), None)


class PostgresIdempotencyStore:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, stale_after: timedelta = STALE_AFTER
    ) -> None:
        self.sessions = sessions
        self.stale_after = stale_after

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
            # Under READ COMMITTED, a retry that waited on this row re-checks the WHERE against
            # the winner's new timestamp and updates nothing.
            reclaimed = await session.execute(
                sa.update(table)
                .where(
                    where,
                    table.c.response_status.is_(None),
                    table.c.request_hash == request_hash,
                    table.c.created_at < sa.func.now() - self.stale_after,
                )
                .values(created_at=sa.func.now())
                .returning(table.c.key)
            )
            if reclaimed.first() is not None:
                return Outcome("new")
            row = (await session.execute(sa.select(table).where(where))).mappings().one()
        return _decide(
            row["request_hash"], request_hash, row["response_status"], row["response_body"]
        )

    async def renew(self, scope: UUID, key: str) -> None:
        table = idempotency_keys
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(table)
                .where(
                    table.c.seller_id == scope,
                    table.c.key == key,
                    table.c.response_status.is_(None),
                )
                .values(created_at=sa.func.now())
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


ScopeResolver = Callable[[Request], UUID | Awaitable[UUID | None] | None]


def _anonymous(_request: Request) -> None:
    return None


class IdempotencyMiddleware:
    """``scope`` maps a request to the owner of its key, from its headers alone; None is no one."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: IdempotencyStore,
        scope: ScopeResolver = _anonymous,
        renew_every_s: float = RENEW_EVERY_S,
    ) -> None:
        self.app = app
        self.store = store
        self.scope = scope
        self.renew_every_s = renew_every_s

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

        principal = self.scope(Request(scope))
        owner = await principal if inspect.isawaitable(principal) else principal
        if owner is None:
            await self.app(scope, receive, send)
            return

        body = await _read_body(receive)
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

        renewal = asyncio.create_task(self._keep_renewing(owner, key))
        try:
            await self.app(scope, _replay(body), capture)
        except Exception:
            await self.store.abandon(owner, key)
            raise
        finally:
            renewal.cancel()
        status = captured["status"]
        # A refused call says nothing about the key: the caller may retry it with credentials.
        if status >= 500 or status in {401, 403, 429} or not captured["json"]:
            await self.store.abandon(owner, key)
            return
        await self.store.finish(
            owner, key, status, json.loads(b"".join(captured["chunks"]) or b"null")
        )

    async def _keep_renewing(self, owner: UUID, key: str) -> None:
        while True:
            await asyncio.sleep(self.renew_every_s)
            try:
                await self.store.renew(owner, key)
            except Exception:
                logger.warning("could not renew an idempotency key", exc_info=True)


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
