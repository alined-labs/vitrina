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
    assert update.phone_number_id == "100000000000001"
    assert update.occurred_at.tzinfo is UTC
    assert update.errors == ()


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
        (
            b'{"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field":'
            b' "messages", "value": {"metadata": {"display_phone_number": "573000000000",'
            b' "phone_number_id": "1"}, "messages": [{"from": "573000000001", "type": "text"}]}}]}]}'
        ),
    ],
    ids=["not-json", "not-an-object", "other-product", "no-entries", "message-without-id"],
)
def test_as_3_1_malformed_payload_is_rejected(body: bytes) -> None:
    with pytest.raises(WebhookPayloadError):
        parse_webhook(body)


def test_as_3_1_payload_errors_never_echo_customer_data() -> None:
    body = (
        b'{"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field":'
        b' "messages", "value": {"metadata": {"display_phone_number": "573000000000",'
        b' "phone_number_id": "1"}, "messages": [{"from": 573000000001, "id": "x",'
        b' "timestamp": "1", "type": "text"}]}}]}]}'
    )

    with pytest.raises(WebhookPayloadError) as caught:
        parse_webhook(body)

    assert "573000000001" not in str(caught.value)


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
