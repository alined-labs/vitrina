"""Brand voice from an exported WhatsApp chat (T022, AS-1.5, FR-004).

Only the seller's own messages are kept (customer lines are discarded before anything is
stored), PII is redacted with patterns (the self-hosted guardian can refine it later,
constitution Art. V.2), and the profile comes from deterministic signals: usted vs. tú,
warmth markers, emoji rate and Colombian regionalisms.
"""

import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime

from vitrina_catalog.sellers import BrandVoiceProfile

# Android: "27/10/26, 14:05 - Ana: Hola"  ·  iOS: "[27/10/26, 2:05:33 p. m.] Ana: Hola"
_MESSAGE = re.compile(
    r"^\[?\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?\]?\s*(?:-\s*)?"
    r"(?P<author>[^:\n]{1,80}):\s(?P<text>.*)$",
    re.IGNORECASE,
)
_STARTS_A_MESSAGE = re.compile(r"^\[?\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}")
_MEDIA = re.compile(
    r"^<?(multimedia omitido|media omitted|imagen omitida|audio omitido|sticker omitido)>?$", re.I
)

_PHONE = re.compile(r"(?<!\w)(?:\+?57[\s.-]?)?3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_HANDLE = re.compile(r"(?<![\w@])@[\w.]{2,30}")
_LONG_NUMBER = re.compile(r"(?<![\d.,$])\d{6,}(?![\d.,])")

_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿❤]")
_DIMINUTIVE = re.compile(r"\b\w{3,}(?:ito|ita|itos|itas)\b")
_USTED = re.compile(r"\b(usted|ustedes|le|les|su|sus|senor|senora|buenos dias|buenas tardes)\b")
_TU = re.compile(
    r"\b(tu|tus|te|ti|tienes|quieres|vas|puedes|escribeme|mandame|cuentame|veci|parce|mija|mijo)\b"
)
_AFFECTION = (
    "veci",
    "parce",
    "mi reina",
    "mi amor",
    "corazon",
    "querida",
    "linda",
    "amiga",
    "mija",
    "mijo",
    "sumerce",
)
REGIONALISMS = (
    "veci", "parce", "a la orden", "con mucho gusto", "de una", "listo", "bacano", "chevere", "que mas",
    "mija", "mijo", "sumerce", "mi reina", "mi amor", "regalame", "regaleme", "pues",
)  # fmt: skip
_GREETINGS = ("hola", "buenas", "buenos", "buen", "que mas", "quiubo")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in folded if not unicodedata.combining(char))


def seller_lines(export: str, seller_display_name: str) -> list[str]:
    """The seller's messages, continuation lines joined; system, media and customer lines dropped."""
    wanted = _plain(seller_display_name).strip()
    messages: list[tuple[str, str]] = []
    for raw in export.splitlines():
        line = raw.strip("‎﻿ ")
        match = _MESSAGE.match(line)
        if match:
            messages.append((_plain(match["author"]).strip(), match["text"].strip()))
        elif line and messages and not _STARTS_A_MESSAGE.match(line):
            author, text = messages[-1]
            messages[-1] = (author, f"{text}\n{line}")
    return [
        text for author, text in messages if author == wanted and text and not _MEDIA.match(text)
    ]


def redact(text: str) -> str:
    """Phone numbers, emails, links, handles and long digit runs (IDs, accounts) go; prices stay."""
    for pattern, placeholder in (
        (_URL, "[enlace]"),
        (_EMAIL, "[correo]"),
        (_PHONE, "[teléfono]"),
        (_HANDLE, "[usuario]"),
        (_LONG_NUMBER, "[número]"),
    ):
        text = pattern.sub(placeholder, text)
    return text


def _scale(value: float) -> float:
    return round(min(5.0, max(1.0, value)), 1)


def voice_profile(lines: list[str]) -> BrandVoiceProfile:
    plain = [_plain(line) for line in lines]
    count = len(lines) or 1
    usted = sum(len(_USTED.findall(line)) for line in plain)
    tu = sum(len(_TU.findall(line)) for line in plain)
    formality = 3.0 if usted + tu == 0 else 3 + 2 * (usted - tu) / (usted + tu)

    def warmth_points(original: str, folded: str) -> int:
        return (
            bool(_EMOJI.search(original))
            + ("!" in original or "¡" in original)
            + bool(_DIMINUTIVE.search(folded))
            + any(word in folded for word in _AFFECTION)
        )

    warmth = 1 + 2 * sum(warmth_points(o, f) for o, f in zip(lines, plain, strict=True)) / count
    found = Counter(
        phrase
        for line in plain
        for phrase in REGIONALISMS
        if re.search(rf"\b{re.escape(phrase)}\b", line)
    )
    greetings = Counter(
        " ".join(re.findall(r"[a-zñ]+", line)[:2]) for line in plain if line.startswith(_GREETINGS)
    )
    return BrandVoiceProfile(
        formality=_scale(formality),
        warmth=_scale(warmth),
        emoji_rate=round(sum(bool(_EMOJI.search(line)) for line in lines) / count, 3),
        regionalisms=[phrase for phrase, _ in found.most_common(8)],
        greeting_style=greetings.most_common(1)[0][0] if greetings else "",
        sample_count=len(lines),
        updated_at=datetime.now(UTC),
    )
