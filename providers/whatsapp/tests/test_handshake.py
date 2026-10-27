"""FR-020: the GET handshake echoes hub.challenge only for Meta's subscribe request with our token."""

import pytest

from vitrina_whatsapp.handshake import verify_subscription

TOKEN = "fixture-verify-token"
CHALLENGE = "1158201444"


def test_fr_020_handshake_echoes_the_challenge() -> None:
    assert verify_subscription("subscribe", TOKEN, CHALLENGE, verify_token=TOKEN) == CHALLENGE


@pytest.mark.parametrize(
    ("mode", "token", "challenge"),
    [
        ("subscribe", "wrong-token", CHALLENGE),
        ("unsubscribe", TOKEN, CHALLENGE),
        (None, TOKEN, CHALLENGE),
        ("subscribe", None, CHALLENGE),
        ("subscribe", TOKEN, None),
    ],
)
def test_fr_020_handshake_refuses_anything_else(
    mode: str | None, token: str | None, challenge: str | None
) -> None:
    assert verify_subscription(mode, token, challenge, verify_token=TOKEN) is None


def test_fr_020_handshake_is_refused_while_the_verify_token_is_unset() -> None:
    assert verify_subscription("subscribe", "", CHALLENGE, verify_token="") is None
