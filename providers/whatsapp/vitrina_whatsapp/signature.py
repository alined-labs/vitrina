"""X-Hub-Signature-256 check for WhatsApp Cloud API webhooks (FR-021, AS-3.1).

Meta signs every notification with HMAC-SHA256 keyed by the app secret and sends
``sha256=<hex digest>``. The digest covers the body bytes exactly as received: verify before
parsing, never against a re-serialized payload (Meta escapes non-ASCII as ``\\uXXXX``).
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
_SCHEME = "sha256"


def sign(body: bytes, app_secret: str) -> str:
    """The header value Meta sends for ``body``; also signs recorded fixtures in tests."""
    return f"{_SCHEME}={_digest(body, app_secret)}"


def is_valid_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """True only if ``header`` is ``sha256=`` plus the HMAC of ``body`` under ``app_secret``.

    An unset app secret never validates: an HMAC keyed by the empty string is forgeable.
    """
    if not app_secret or not header:
        return False
    scheme, _, received = header.strip().partition("=")
    if scheme.lower() != _SCHEME or not received.isascii():
        return False
    return hmac.compare_digest(_digest(body, app_secret), received.lower())


def _digest(body: bytes, app_secret: str) -> str:
    return hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
