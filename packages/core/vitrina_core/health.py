"""Provider health for ``GET /health/providers`` (NFR-02, T073): one row per dependency.

A row is ``ok``, ``degraded`` (it answers, with errors or a backlog), ``down`` (no usable
answer) or ``mocked`` (replay mode, the local WhatsApp mock or in-memory stores: nothing to
reach). The checks run together, each within ``timeout_s``, and a check that raises is a
``down`` row, never a failed report. ``HealthCache`` keeps the last report for a while, so a
dashboard that polls does not call every provider each time.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Literal, NamedTuple, cast

import httpx2
import sqlalchemy as sa
from pydantic import MISSING, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.jobs import PostgresJobQueue

HealthStatus = Literal["ok", "degraded", "down", "mocked"]


class Outcome(NamedTuple):
    status: HealthStatus
    detail: str | None = None


Check = Callable[[], Awaitable[Outcome]]


class ProviderHealth(BaseModel):
    """One row of openapi.yaml ``getProviderHealth``."""

    status: HealthStatus
    latency_ms: int | MISSING = MISSING
    checked_at: datetime | MISSING = MISSING
    detail: str | MISSING = MISSING


def mocked(detail: str) -> Check:
    async def check() -> Outcome:
        return Outcome("mocked", detail)

    return check


def fixed(outcome: Outcome) -> Check:
    """A row known without a call, e.g. a provider that is not configured."""

    async def check() -> Outcome:
        return outcome

    return check


async def run_checks(
    checks: Mapping[str, Check], *, timeout_s: float, now: Callable[[], datetime]
) -> dict[str, ProviderHealth]:
    async def one(check: Check) -> ProviderHealth:
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout_s):
                outcome = await check()
        except TimeoutError:
            outcome = Outcome("down", f"no answer within {timeout_s:g} s")
        except Exception as error:  # the class only: messages may carry URLs or keys
            outcome = Outcome("down", type(error).__name__)
        return ProviderHealth(
            status=outcome.status,
            latency_ms=(
                MISSING
                if outcome.status == "mocked"
                else round((time.monotonic() - started) * 1000)
            ),
            checked_at=now(),
            detail=MISSING if outcome.detail is None else outcome.detail,
        )

    rows = await asyncio.gather(*(one(check) for check in checks.values()))
    return dict(zip(checks, rows, strict=True))


class HealthCache:
    """The last report, reused for ``ttl_s`` seconds."""

    def __init__(self, ttl_s: float, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self._monotonic = monotonic
        self._report: Any = None
        self._expires = 0.0

    async def get[T](self, build: Callable[[], Awaitable[T]]) -> T:
        if self._report is None or self._monotonic() >= self._expires:
            self._report = await build()
            self._expires = self._monotonic() + self.ttl_s
        return cast(T, self._report)


async def http_check(
    client: httpx2.AsyncClient, url: str, *, headers: Mapping[str, str]
) -> Outcome:
    """A GET on a resource that needs credentials: 2xx is ok, 429 and 5xx degraded, the rest
    (a refused key, a wrong id) down."""
    try:
        response = await client.get(url, headers=dict(headers))
    except httpx2.TransportError:
        return Outcome("down", "no connection")
    code = response.status_code
    if code < 300:
        return Outcome("ok")
    return Outcome("degraded" if code == 429 or code >= 500 else "down", f"HTTP {code}")


async def reachable(client: httpx2.AsyncClient, url: str) -> Outcome:
    """Any answer below 500 means the service is up: an inference URL may refuse a bare GET."""
    try:
        response = await client.get(url)
    except httpx2.TransportError:
        return Outcome("down", "no connection")
    if response.status_code >= 500:
        return Outcome("degraded", f"HTTP {response.status_code}")
    return Outcome("ok")


def database_check(sessions: async_sessionmaker[AsyncSession]) -> Check:
    async def check() -> Outcome:
        async with sessions() as session:
            await session.execute(sa.text("SELECT 1"))
        return Outcome("ok")

    return check


def queue_check(queue: PostgresJobQueue, *, stalled_after_s: float) -> Check:
    """Degraded once a due job has waited longer than the worker's stall threshold: no worker
    is taking jobs, or one long job holds it. Counts stay out: they span every seller."""

    async def check() -> Outcome:
        _, waited_s = await queue.backlog()
        if waited_s > stalled_after_s:
            return Outcome("degraded", f"a due job has waited {waited_s:.0f} s for a worker")
        return Outcome("ok")

    return check
