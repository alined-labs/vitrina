"""AS-3.1, FR-021: X-Hub-Signature-256 carries the HMAC-SHA256 of the raw body under the app secret."""

from collections.abc import Callable

import pytest

from vitrina_whatsapp.signature import is_valid_signature, sign

SECRET = "fixture-app-secret"


def test_as_3_1_valid_signature_is_accepted(webhook_body: Callable[[str], bytes]) -> None:
    body = webhook_body("text_ad_code_a7")

    assert sign(body, SECRET).startswith("sha256=")
    assert is_valid_signature(body, sign(body, SECRET), SECRET)


def test_as_3_1_uppercase_hex_digest_is_accepted(webhook_body: Callable[[str], bytes]) -> None:
    body = webhook_body("text_ad_code_a7")
    digest = sign(body, SECRET).removeprefix("sha256=")

    assert is_valid_signature(body, f"sha256={digest.upper()}", SECRET)


def test_as_3_1_tampered_body_is_rejected(webhook_body: Callable[[str], bytes]) -> None:
    body = webhook_body("text_ad_code_a7")
    header = sign(body, SECRET)

    assert not is_valid_signature(body.replace(b"#A7", b"#A8"), header, SECRET)


def test_as_3_1_signature_under_another_secret_is_rejected(
    webhook_body: Callable[[str], bytes],
) -> None:
    body = webhook_body("text_ad_code_a7")

    assert not is_valid_signature(body, sign(body, "another-app-secret"), SECRET)


def test_as_3_1_bare_digest_without_scheme_is_rejected(
    webhook_body: Callable[[str], bytes],
) -> None:
    body = webhook_body("text_ad_code_a7")

    assert not is_valid_signature(body, sign(body, SECRET).removeprefix("sha256="), SECRET)


@pytest.mark.parametrize(
    "header",
    [None, "", "sha256=", "sha1=" + "0" * 40, "sha256=not-a-hex-digest", "sha256=añ"],
)
def test_as_3_1_missing_or_malformed_header_is_rejected(
    header: str | None, webhook_body: Callable[[str], bytes]
) -> None:
    assert not is_valid_signature(webhook_body("text_ad_code_a7"), header, SECRET)


def test_as_3_1_unset_app_secret_never_validates(webhook_body: Callable[[str], bytes]) -> None:
    body = webhook_body("text_ad_code_a7")

    assert not is_valid_signature(body, sign(body, ""), "")
