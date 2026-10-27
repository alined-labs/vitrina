"""FR-022: inbound messages in the canonical form the agent consumes, keyword flags first.

Opt-out and erasure keywords (R7, FR-063) and an affirmative answer to a pending consent
prompt (R8) are flagged here, before any model sees the message.
"""

import pytest

from vitrina_conversation.normalize import normalize

PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"


def test_fr_022_a_text_message_is_normalized_with_its_ad_code() -> None:
    message = normalize("text", "Hola, vi el anuncio #A7")

    assert (message.kind, message.text, message.language) == (
        "text",
        "Hola, vi el anuncio #A7",
        "es",
    )
    assert message.ad_code == "A7"
    assert (message.opt_out_detected, message.erasure_requested, message.consent_affirmative) == (
        False,
        False,
        False,
    )


def test_fr_022_a_message_without_a_code_has_a_null_ad_code() -> None:
    assert normalize("text", "¿cuánto vale?").ad_code is None


@pytest.mark.parametrize("text", ["BAJA", "baja por favor", "Ya no más 🙏", "STOP"])
def test_as_6_5_opt_out_keywords_are_flagged_before_anything_else(text: str) -> None:
    assert normalize("text", text).opt_out_detected


def test_as_6_5_cancelling_an_order_is_not_an_opt_out() -> None:
    assert not normalize("text", "quiero cancelar mi pedido").opt_out_detected


def test_as_6_6_an_erasure_request_is_flagged() -> None:
    assert normalize("text", "BORRAR MIS DATOS").erasure_requested


def test_as_6_1_a_yes_counts_only_after_a_consent_prompt() -> None:
    assert normalize("text", "Sí, dale", pending_consent_prompt=PROMPT).consent_affirmative
    assert not normalize("text", "Sí, dale").consent_affirmative
    assert not normalize(
        "text", "si me mandas fotos", pending_consent_prompt=PROMPT
    ).consent_affirmative


@pytest.mark.parametrize(
    ("kind", "text", "normalized_kind"),
    [
        ("interactive", "Reservar", "interactive"),
        ("image", "¿Lo tienen en rojo?", "image"),
        ("audio", None, "audio"),
        ("video", None, "unsupported"),
        ("document", "factura.pdf", "unsupported"),
        ("unsupported", None, "unsupported"),
    ],
)
def test_fr_022_every_kind_maps_to_a_normalized_kind(
    kind: str, text: str | None, normalized_kind: str
) -> None:
    message = normalize(kind, text)

    assert message.kind == normalized_kind
    assert message.text == (text or "")


def test_fr_022_the_json_matches_the_contract_shape() -> None:
    dumped = normalize("text", "Hola, vi el anuncio #A7").model_dump(mode="json")

    assert dumped == {
        "kind": "text",
        "text": "Hola, vi el anuncio #A7",
        "language": "es",
        "ad_code": "A7",
        "opt_out_detected": False,
        "erasure_requested": False,
        "consent_affirmative": False,
    }
