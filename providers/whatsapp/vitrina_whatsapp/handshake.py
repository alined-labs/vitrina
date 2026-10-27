"""Meta's webhook verification handshake for ``GET /webhooks/whatsapp`` (FR-020).

When the callback URL is saved in the app dashboard, Meta sends ``hub.mode=subscribe``, the
verify token configured there and a ``hub.challenge`` that the endpoint must echo as plain text.
Anything else gets ``403``.
"""

import hmac

SUBSCRIBE = "subscribe"


def verify_subscription(
    mode: str | None, token: str | None, challenge: str | None, *, verify_token: str
) -> str | None:
    """The challenge to echo, or None when the request is not Meta's subscribe call for us."""
    if not verify_token or mode != SUBSCRIBE or token is None or challenge is None:
        return None
    if not hmac.compare_digest(token.encode(), verify_token.encode()):
        return None
    return challenge
