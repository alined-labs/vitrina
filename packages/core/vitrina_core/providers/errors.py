"""Errors every provider adapter raises, so callers handle them the same way."""


class ProviderError(Exception):
    """A provider call failed. ``retryable`` marks failures worth a backoff retry."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code


class ProviderUnavailableError(ProviderError):
    """Timeout, connection error or 5xx: the API answers 503 PROVIDER_UNAVAILABLE."""

    def __init__(self, message: str, *, provider: str, status_code: int | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True, status_code=status_code)


class RateLimitedError(ProviderError):
    """429 from the provider; ``retry_after_s`` comes from its Retry-After header."""

    def __init__(self, message: str, *, provider: str, retry_after_s: float | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True, status_code=429)
        self.retry_after_s = retry_after_s


class CassetteMissError(ProviderError):
    """Replay mode found no recorded response for this request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, provider="local_mock")
