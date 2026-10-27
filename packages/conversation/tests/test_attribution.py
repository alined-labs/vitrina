"""AS-3.2, FR-023: the ``#A..`` code in a customer's message attributes the conversation."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from vitrina_conversation.attribution import (
    NO_ATTRIBUTION,
    Attribution,
    attribute,
    chat_started_payload,
    parse_ad_code,
)

SELLER = uuid4()
AD_A7 = uuid4()


async def lookup(seller_id: UUID, code: str) -> UUID | None:
    return AD_A7 if (seller_id, code) == (SELLER, "A7") else None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Hola, vi el anuncio #A7", "A7"),
        ("vi el anuncio #ak2, ¿cuánto vale?", "AK2"),
        ("¿Precio? #A7.", "A7"),
        ("#A7 y también #A8", "A7"),
        ("Hola, ¿tienen mochilas?", None),
        ("#A1234 no es un código", None),
        ("#B7 tampoco", None),
        ("A7 sin numeral", None),
        ("#A", None),
    ],
)
def test_fr_023_the_first_ad_code_is_parsed(text: str, code: str | None) -> None:
    assert parse_ad_code(text) == code


async def test_as_3_2_a_known_code_attributes_the_conversation() -> None:
    result, started = await attribute(NO_ATTRIBUTION, "Hola, vi el anuncio #A7", SELLER, lookup)

    assert result == Attribution("attributed", AD_A7, "A7")
    assert started


async def test_fr_023_an_unknown_code_is_recorded_as_such() -> None:
    result, started = await attribute(NO_ATTRIBUTION, "vi el anuncio #A9", SELLER, lookup)

    assert result == Attribution("unknown_code", None, "A9")
    assert not started


async def test_fr_023_a_later_message_can_still_attribute() -> None:
    unknown = Attribution("unknown_code", None, "A9")

    after_unknown, started = await attribute(unknown, "perdón, era el #A7", SELLER, lookup)
    after_nothing, _ = await attribute(NO_ATTRIBUTION, "es el #A7", SELLER, lookup)

    assert (after_unknown, started) == (Attribution("attributed", AD_A7, "A7"), True)
    assert after_nothing.status == "attributed"


async def test_fr_023_the_first_attribution_stays() -> None:
    first = Attribution("attributed", AD_A7, "A7")

    result, started = await attribute(first, "y el #A8 también", SELLER, lookup)

    assert (result, started) == (first, False)


async def test_fr_023_a_message_without_a_code_changes_nothing() -> None:
    result, started = await attribute(NO_ATTRIBUTION, "¿cuánto vale?", SELLER, lookup)

    assert (result, started) == (NO_ATTRIBUTION, False)


async def test_fr_023_codes_belong_to_their_seller() -> None:
    result, _ = await attribute(NO_ATTRIBUTION, "vi el anuncio #A7", uuid4(), lookup)

    assert result.status == "unknown_code"


def test_as_3_2_chat_started_payload_follows_data_model_events() -> None:
    conversation, contact = uuid4(), uuid4()
    at = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)

    payload = chat_started_payload(AD_A7, conversation, contact, at)

    assert payload == {
        "ad_id": str(AD_A7),
        "conversation_id": str(conversation),
        "contact_id": str(contact),
        "occurred_at": "2026-10-27T15:00:00Z",
    }
