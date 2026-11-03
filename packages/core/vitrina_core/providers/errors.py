"""Errors every provider adapter raises, so callers handle them the same way."""

from typing import Literal

ErrorScope = Literal["recipient", "account", "unknown"]
"""Who else the failure would hit: this recipient alone, every call on the account, or unknown."""


class ProviderError(Exception):
    """A provider call failed. ``retryable`` marks failures worth a backoff retry.

    ``scope`` lets a caller working through many recipients tell one refusal from an outage;
    ``retry_after_s`` is the pause the provider asked for, when it asked for one.
    ``nothing_sent`` is True only when the provider's answer proves the message did not go out; by
    default a failure may have delivered it, which is what a caller must not repeat.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
        status_code: int | None = None,
        scope: ErrorScope = "unknown",
        retry_after_s: float | None = None,
        nothing_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.scope = scope
        self.retry_after_s = retry_after_s
        self.nothing_sent = nothing_sent


class ProviderUnavailableError(ProviderError):
    """Timeout, connection error or 5xx: the API answers 503 PROVIDER_UNAVAILABLE."""

    def __init__(self, message: str, *, provider: str, status_code: int | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True, status_code=status_code)


class RateLimitedError(ProviderError):
    """429 from the provider; ``retry_after_s`` comes from its Retry-After header."""

    def __init__(self, message: str, *, provider: str, retry_after_s: float | None = None) -> None:
        super().__init__(
            message,
            provider=provider,
            retryable=True,
            status_code=429,
            scope="account",
            retry_after_s=retry_after_s,
            nothing_sent=True,
        )


class CassetteMissError(ProviderError):
    """Replay mode found no recorded response for this request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, provider="local_mock")
