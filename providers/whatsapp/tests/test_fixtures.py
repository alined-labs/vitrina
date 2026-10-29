"""Art. V.1, AS-3.1: recorded payloads carry only synthetic contacts and all of them parse."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from vitrina_whatsapp.webhook import parse_webhook

FIXTURES = sorted((Path(__file__).resolve().parents[3] / "evals/fixtures/webhooks").glob("*.json"))
SYNTHETIC_NUMBER = re.compile(r"57300000\d{4}")
PHONE_FIELDS = {"from", "wa_id", "recipient_id", "display_phone_number", "input"}
# Business-scoped user IDs (usernames) are fixture ids too, never a real user's.
SYNTHETIC_USER_ID = re.compile(r"CO\.VITRINAFIXTUREUSER\d{4}")
USER_ID_FIELDS = {"from_user_id", "user_id", "recipient_user_id"}
FREE_TEXT_FIELDS = {"body", "caption", "title", "text", "description", "name", "details"}
DIGIT_RUN = re.compile(r"\d{7,}")


def leaves(node: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for child_key, child in node.items():
            yield from leaves(child, child_key)
    elif isinstance(node, list):
        for child in node:
            yield from leaves(child, key)
    else:
        yield key, node


def test_as_3_1_recorded_payloads_are_present() -> None:
    assert len(FIXTURES) >= 10


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_art_v_1_fixture_contacts_are_synthetic(path: Path) -> None:
    for key, value in leaves(json.loads(path.read_bytes())):
        if key in PHONE_FIELDS:
            assert SYNTHETIC_NUMBER.fullmatch(str(value)), (
                f"{path.name}: {key} is not +57300000xxxx"
            )
        elif key in USER_ID_FIELDS:
            assert SYNTHETIC_USER_ID.fullmatch(str(value)), f"{path.name}: {key} is not synthetic"
        elif key in FREE_TEXT_FIELDS and isinstance(value, str):
            assert not DIGIT_RUN.search(value), f"{path.name}: {key} contains a long digit run"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_as_3_1_every_recorded_payload_parses(path: Path) -> None:
    batch = parse_webhook(path.read_bytes())

    assert batch.messages or batch.statuses or batch.ignored_fields
