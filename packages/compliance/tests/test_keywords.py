import pytest

from vitrina_compliance.keywords import is_affirmative, is_erasure_request, is_opt_out_request


@pytest.mark.parametrize(
    "text",
    ["BAJA", "baja", "Baja.", "  baja!! ", "baja por favor", "No más", "no mas", "NO MÁS 🙏",
     "ya no más", "stop", "STOP", "Stop, gracias", "cancelar", "Cancelar porfa"],
)  # fmt: skip
def test_as_6_5_opt_out_keywords_match_whatever_the_case_accents_or_courtesy(text: str) -> None:
    assert is_opt_out_request(text)


@pytest.mark.parametrize(
    "text",
    ["quiero cancelar mi pedido", "no más de dos, por favor", "¿la baja de precio aplica?",
     "stop motion", "hola", "", "bajá el precio"],
)  # fmt: skip
def test_ordinary_sentences_with_those_words_are_not_opt_outs(text: str) -> None:
    assert not is_opt_out_request(text)


@pytest.mark.parametrize(
    "text", ["BORRAR MIS DATOS", "borrar mis datos", "Borrar mis datos, por favor"]
)
def test_as_6_6_the_erasure_keyword_is_recognized(text: str) -> None:
    assert is_erasure_request(text)


def test_mentioning_data_is_not_an_erasure_request() -> None:
    assert not is_erasure_request("¿qué datos necesitan para el envío?")
    assert not is_opt_out_request("BORRAR MIS DATOS")


@pytest.mark.parametrize(
    "text",
    ["Sí", "si", "SI!", "Sí, por favor", "claro", "sí claro", "dale 👍", "dale de una", "Listo",
     "ok", "de una", "Por favor avísame", "sí pues", "Sí, por favor avísame"],
)  # fmt: skip
def test_as_6_1_affirmative_replies_follow_the_r8_lexicon(text: str) -> None:
    assert is_affirmative(text)


@pytest.mark.parametrize(
    "text",
    ["no", "No gracias", "si no es molestia", "claro que no", "mmm", "", "¿cuánto vale?",
     "tal vez", "ok no",
     # C10 (MX1): a lexicon word at the start is not enough; the whole reply must be consent.
     "si me mandas fotos del bolso", "si quieres", "si, pero solo los martes",
     "listo, cuánto vale?", "ok gracias", "Claro que sí", "sí quiero"],
)  # fmt: skip
def test_anything_else_is_not_consent(text: str) -> None:
    # ADR-0009: ambiguous replies are rejected by design; the agent asks again.
    assert not is_affirmative(text)


def test_a_reply_of_a_thousand_words_is_not_consent_and_does_not_crash() -> None:
    # MX1: a customer can send "si si si …" × 1000 inside one WhatsApp message (4,096 chars).
    assert not is_affirmative(" ".join(["si"] * 1000))
    assert not is_affirmative("sí " * 2000)
