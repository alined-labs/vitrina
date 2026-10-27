"""Exponential backoff for provider calls (constitution Art. VIII.1, R12: max 3 attempts)."""

import asyncio
from collections.abc import Awaitable, Callable

from vitrina_core.providers.errors import ProviderError, RateLimitedError


def is_retryable(error: BaseException) -> bool:
    return isinstance(error, ProviderError) and error.retryable


async def with_backoff[T](
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_s: float = 0.5,
    max_delay_s: float = 8.0,
    retry_if: Callable[[BaseException], bool] = is_retryable,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``call``; retry retryable failures with delays 0.5 s, 1 s, 2 s … (Retry-After wins)."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as error:
            if attempt == attempts or not retry_if(error):
                raise
            delay = base_delay_s * 2 ** (attempt - 1)
            if isinstance(error, RateLimitedError) and error.retry_after_s is not None:
                delay = max(delay, error.retry_after_s)
            await sleep(min(delay, max_delay_s))
    raise AssertionError("unreachable: the last attempt returns or raises")
