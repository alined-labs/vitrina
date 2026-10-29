"""AS-3.1, FR-021, FR-028: recorded webhook payloads become inbound messages and status updates."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vitrina_whatsapp.webhook import (
    DeliveryStatus,
    MediaRef,
    WebhookPayloadError,
    WebhookStatus,
    merge_delivery_status,
    parse_webhook,
)

LAURA = "573000000001"
ANDRES = "573000000002"
SENT_REPLY = "wamid.VITRINA-FIXTURE-OUT-0001"


def test_as_3_1_text_message_carries_ids_sender_and_utc_time(
    webhook_body: Callable[[str], bytes],
) -> None:
    batch = parse_webhook(webhook_body("text_ad_code_a7"))

    (message,) = batch.messages
    assert batch.statuses == ()
    assert message.message_id == "wamid.VITRINA-FIXTURE-IN-0001"
    assert message.phone_number_id == "100000000000001"
    assert message.business_number == "573000000000"
    assert message.from_wa_id == LAURA
    assert message.profile_name == "Laura Prueba"
    assert message.sent_at == datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    assert (message.wa_type, message.kind) == ("text", "text")
    assert message.text == "Hola, vi el anuncio #A7"


def test_as_3_1_spanish_text_is_decoded_from_meta_unicode_escapes(
    webhook_body: Callable[[str], bytes],
) -> None:
    (message,) = parse_webhook(webhook_body("text_question_shipping")).messages

    assert message.text == "¿cuánto vale el envío a Medellín?"


def test_as_3_1_batch_exposes_every_message_id_for_dedupe(
    webhook_body: Callable[[str], bytes],
) -> None:
    batch = parse_webhook(webhook_body("batch_two_customers"))

    assert batch.message_ids == ("wamid.VITRINA-FIXTURE-IN-0011", "wamid.VITRINA-FIXTURE-IN-0012")
    # The fixture lists contacts in reverse order: names are matched by wa_id, not position.
    assert [(m.from_wa_id, m.profile_name) for m in batch.messages] == [
        (LAURA, "Laura Prueba"),
        (ANDRES, "Andrés Prueba"),
    ]


def test_as_3_1_payload_parses_from_bytes_text_or_decoded_json(
    webhook_body: Callable[[str], bytes],
) -> None:
    raw = webhook_body("text_ad_code_a7")

    assert parse_webhook(raw) == parse_webhook(raw.decode()) == parse_webhook(json.loads(raw))


@pytest.mark.parametrize(
    ("fixture", "wa_type", "kind", "text", "reply_id"),
    [
        ("image_with_caption", "image", "image", "¿Lo tienen en rojo?", None),
        ("audio_voice_note", "audio", "audio", None, None),
        ("interactive_button_reply", "interactive", "interactive", "Reservar", "reservar"),
        ("interactive_list_reply", "interactive", "interactive", "Talla M", "talla-m"),
        ("button_quick_reply", "button", "interactive", "Ver catálogo", "VER_CATALOGO"),
        ("reaction", "reaction", "unsupported", None, None),
        ("unsupported", "unsupported", "unsupported", None, None),
    ],
)
def test_as_3_1_message_types_map_to_contract_kinds(
    fixture: str,
    wa_type: str,
    kind: str,
    text: str | None,
    reply_id: str | None,
    webhook_body: Callable[[str], bytes],
) -> None:
    (message,) = parse_webhook(webhook_body(fixture)).messages

    assert (message.wa_type, message.kind, message.text, message.reply_id) == (
        wa_type,
        kind,
        text,
        reply_id,
    )


def test_as_3_1_media_references_are_kept_for_download(
    webhook_body: Callable[[str], bytes],
) -> None:
    (image,) = parse_webhook(webhook_body("image_with_caption")).messages
    (voice,) = parse_webhook(webhook_body("audio_voice_note")).messages
    (text,) = parse_webhook(webhook_body("text_ad_code_a7")).messages

    assert image.media is not None
    assert (image.media.media_id, image.media.mime_type, image.media.voice) == (
        "1000000000000101",
        "image/jpeg",
        False,
    )
    assert isinstance(voice.media, MediaRef)
    assert (voice.media.mime_type, voice.media.voice) == ("audio/ogg; codecs=opus", True)
    assert text.media is None


def test_as_3_1_replies_point_at_the_quoted_message(webhook_body: Callable[[str], bytes]) -> None:
    (reply,) = parse_webhook(webhook_body("interactive_button_reply")).messages
    (text,) = parse_webhook(webhook_body("text_ad_code_a7")).messages

    assert reply.context_message_id == SENT_REPLY
    assert text.context_message_id is None


@pytest.mark.parametrize(
    ("fixture", "status"),
    [("status_sent", "sent"), ("status_delivered", "delivered"), ("status_read", "read")],
)
def test_fr_028_status_callbacks_are_parsed(
    fixture: str, status: str, webhook_body: Callable[[str], bytes]
) -> None:
    batch = parse_webhook(webhook_body(fixture))

    (update,) = batch.statuses
    assert batch.messages == ()
    assert (update.message_id, update.recipient_wa_id, update.status) == (SENT_REPLY, LAURA, status)
    assert (update.phone_number_id, update.business_number) == ("100000000000001", "573000000000")
    assert update.occurred_at.tzinfo is UTC
    assert update.errors == ()


# ── usernames: Meta leaves the number out for a user with a username (business-scoped IDs) ──

USERNAME_USER = "CO.VITRINAFIXTUREUSER0001"


def envelope(messages: list[object], statuses: list[object] | None = None) -> bytes:
    value = {
        "metadata": {"display_phone_number": "573000000000", "phone_number_id": "1"},
        "messages": messages,
        "statuses": statuses or [],
    }
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
        }
    ).encode()


def text(message_id: str, **sender: object) -> dict[str, object]:
    return {
        "id": message_id,
        "timestamp": "1793113800",
        "type": "text",
        "text": {"body": "Hola"},
        **sender,
    }


def test_as_3_1_a_sender_with_a_username_is_read_by_their_user_id(
    webhook_body: Callable[[str], bytes],
) -> None:
    (message,) = parse_webhook(webhook_body("text_from_username")).messages

    assert message.message_id == "wamid.VITRINA-FIXTURE-IN-0013"
    assert message.from_wa_id is None
    assert message.from_user_id == USERNAME_USER
    assert message.profile_name == "Camila Prueba"
    assert message.text == "Hola, ¿tienen la mochila azul?"


def test_as_3_1_a_sender_with_a_number_keeps_their_user_id_too() -> None:
    (message,) = parse_webhook(
        envelope([text("wamid.A", **{"from": LAURA, "from_user_id": USERNAME_USER})])
    ).messages

    assert (message.from_wa_id, message.from_user_id) == (LAURA, USERNAME_USER)


def test_as_3_1_one_unreadable_message_does_not_drop_the_delivery() -> None:
    body = envelope(
        [
            {"from": LAURA, "type": "text"},  # no id, no timestamp
            text("wamid.B", **{"from": ANDRES}),
            text("wamid.C"),  # no sender at all
            text("wamid.D", **{"from": 573000000003}),  # a number, not a string
        ]
    )

    batch = parse_webhook(body)

    assert batch.message_ids == ("wamid.B",)
    assert len(batch.unreadable) == 3
    assert batch.unreadable[0].startswith("messages.0: ")
    assert "neither from nor from_user_id" in batch.unreadable[1]
    assert all("573000000003" not in problem for problem in batch.unreadable)


def test_fr_028_a_status_carries_the_user_id_and_the_new_fields_are_ignored(
    webhook_body: Callable[[str], bytes],
) -> None:
    (update,) = parse_webhook(webhook_body("status_delivered_user_ids")).statuses

    assert (update.message_id, update.status) == ("wamid.VITRINA-FIXTURE-OUT-0003", "delivered")
    assert (update.recipient_wa_id, update.recipient_user_id) == (
        LAURA,
        "CO.VITRINAFIXTUREUSER0002",
    )


def test_fr_028_a_status_without_the_number_and_an_unreadable_one_still_parse() -> None:
    sent = {"id": "wamid.OUT", "status": "sent", "timestamp": "1793113800"}
    body = envelope(
        [],
        [
            {**sent, "recipient_user_id": USERNAME_USER},
            {"id": "wamid.BROKEN", "status": "read"},  # no timestamp
        ],
    )

    batch = parse_webhook(body)

    (update,) = batch.statuses
    assert (update.recipient_wa_id, update.recipient_user_id) == (None, USERNAME_USER)
    assert batch.unreadable[0].startswith("statuses.1: ")


def test_fr_028_failed_status_carries_the_meta_error(webhook_body: Callable[[str], bytes]) -> None:
    (update,) = parse_webhook(webhook_body("status_failed_reengagement")).statuses

    assert update.status == "failed"
    (error,) = update.errors
    assert error.code == 131047
    assert error.details is not None
    assert "24 hours" in error.details


def test_as_3_1_other_webhook_fields_are_ignored_not_rejected(
    webhook_body: Callable[[str], bytes],
) -> None:
    batch = parse_webhook(webhook_body("template_status_update"))

    assert (batch.messages, batch.statuses) == ((), ())
    assert batch.ignored_fields == ("message_template_status_update",)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"object": "page", "entry": []}',
        b'{"object": "whatsapp_business_account"}',
        b'{"object": "whatsapp_business_account", "entry": [{"id": 573000000001}]}',
    ],
    ids=["not-json", "not-an-object", "other-product", "no-entries", "entry-id-not-a-string"],
)
def test_as_3_1_malformed_payload_is_rejected(body: bytes) -> None:
    with pytest.raises(WebhookPayloadError):
        parse_webhook(body)


def test_as_3_1_payload_errors_never_echo_customer_data() -> None:
    with pytest.raises(WebhookPayloadError) as caught:
        parse_webhook(b'{"object": "whatsapp_business_account", "entry": [{"id": 573000000001}]}')

    assert "573000000001" not in str(caught.value)


def test_as_3_1_a_change_without_metadata_is_listed_not_fatal() -> None:
    body = (
        b'{"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field":'
        b' "messages", "value": {"messages": []}}]}]}'
    )

    batch = parse_webhook(body)

    assert batch.messages == ()
    assert batch.unreadable[0].startswith("changes.0: ")


# ── delivery status (FR-028): callbacks arrive late, twice or out of order ──

PROGRESS: list[DeliveryStatus] = ["queued", "sent", "delivered", "read"]
FINAL: set[DeliveryStatus] = {"read", "failed", "mocked"}


@given(st.permutations(["sent", "delivered", "read"]))
def test_fr_028_delivery_status_ends_at_read_in_any_arrival_order(
    arrivals: list[WebhookStatus],
) -> None:
    current: DeliveryStatus = "queued"
    for incoming in arrivals:
        current = merge_delivery_status(current, incoming)

    assert current == "read"


@given(
    st.sampled_from(["queued", "sent", "delivered", "read", "failed", "mocked"]),
    st.lists(st.sampled_from(["sent", "delivered", "read", "failed"])),
)
def test_fr_028_delivery_status_never_moves_backwards(
    start: DeliveryStatus, arrivals: list[WebhookStatus]
) -> None:
    current = start
    for incoming in arrivals:
        merged = merge_delivery_status(current, incoming)
        if current in FINAL:
            assert merged == current
        elif merged != "failed":
            assert PROGRESS.index(merged) >= PROGRESS.index(current)
        current = merged


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        (None, "delivered", "delivered"),
        ("queued", "failed", "failed"),
        ("sent", "failed", "failed"),
        ("delivered", "failed", "delivered"),
        ("failed", "delivered", "failed"),
        ("mocked", "read", "mocked"),
    ],
)
def test_fr_028_failure_only_replaces_an_undelivered_message(
    current: DeliveryStatus | None, incoming: WebhookStatus, expected: DeliveryStatus
) -> None:
    assert merge_delivery_status(current, incoming) == expected
