"""The OpenAI-compatible client for the Meta Model API (ADR-0003) and its error mapping.

SDK retries are off: callers wrap each call in ``with_backoff`` (R12, Art. VIII.1), so one
policy decides every retry. ``PROVIDER_TIMEOUT_S`` bounds each request. Adapters send the
wire JSON through ``client.post`` (the SDK's documented way to send any body), so the tests
check exactly what reaches the endpoint, and every SDK error becomes a ``ProviderError``.
"""

import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx2
import openai
from openai import AsyncOpenAI

from vitrina_core.providers import (
    ProviderError,
    ProviderName,
    ProviderUnavailableError,
    RateLimitedError,
)
from vitrina_core.settings import Settings

REASON_MAX = 300


def meta_client(
    settings: Settings, *, http_client: httpx2.AsyncClient | None = None
) -> AsyncOpenAI:
    if settings.meta_api_key is None:
        raise LookupError("META_API_KEY is not set; replay mode (REPLAY=true) needs no key")
    return AsyncOpenAI(
        api_key=settings.meta_api_key.get_secret_value(),
        base_url=settings.meta_api_base_url,
        timeout=settings.provider_timeout_s,
        max_retries=0,
        http_client=http_client,
    )


async def call[T](provider: ProviderName, request: Callable[[], Awaitable[T]]) -> tuple[T, int]:
    """Run one request; return its result and latency in ms, or raise a ``ProviderError``."""
    started = time.perf_counter()
    try:
        result = await request()
    except openai.APIError as error:
        raise translate(error, provider) from error
    return result, round((time.perf_counter() - started) * 1000)


def tokens(usage: object, field: str) -> int:
    """A usage count, 0 when the endpoint leaves it out: bookkeeping never fails a call."""
    value = getattr(usage, field, None)
    return value if isinstance(value, int) else 0


def translate(error: openai.APIError, provider: ProviderName) -> ProviderError:
    """429 is rate limited, 5xx, 408, timeouts and lost connections are unavailable (both
    retryable); any other answer is final and keeps the provider's reason (ADR-0005 reads it).
    """
    if isinstance(error, openai.APITimeoutError):
        return ProviderUnavailableError(f"{provider} timed out", provider=provider)
    if isinstance(error, openai.APIConnectionError):
        return ProviderUnavailableError(f"{provider} is unreachable: {error}", provider=provider)
    if not isinstance(error, openai.APIStatusError):
        return ProviderError(f"{provider} sent an unusable answer: {error}", provider=provider)
    status = error.status_code
    message = f"{provider} answered {status}: {_reason(error)}"
    if status == 429:
        return RateLimitedError(
            message, provider=provider, retry_after_s=retry_after_s(error.response.headers)
        )
    if status >= 500 or status == 408:
        return ProviderUnavailableError(message, provider=provider, status_code=status)
    return ProviderError(message, provider=provider, status_code=status)


def _reason(error: openai.APIStatusError) -> str:
    body = error.body
    if isinstance(body, dict):
        body = body.get("error", body)
    text = body.get("message") if isinstance(body, dict) else None
    return " ".join(str(text or error.message).split())[:REASON_MAX]


def retry_after_s(headers: httpx2.Headers, *, now: datetime | None = None) -> float | None:
    """Seconds to wait, from ``retry-after-ms`` or ``retry-after`` (seconds or an HTTP date)."""
    if (milliseconds := _seconds(headers.get("retry-after-ms"))) is not None:
        return milliseconds / 1000
    raw = headers.get("retry-after")
    if raw is None:
        return None
    if (seconds := _seconds(raw)) is not None:
        return seconds
    try:
        moment = parsedate_to_datetime(raw)
    except TypeError, ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - (now or datetime.now(UTC))).total_seconds())


def _seconds(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None
