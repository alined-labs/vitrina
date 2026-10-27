"""Who a contact is, without keeping their number in the clear (Art. V; data-model.md §5).

A WhatsApp number is stored twice: as an HMAC-SHA256 with CONTACT_HASH_PEPPER, the only
lookup key, and encrypted with CONTACT_ENCRYPTION_KEY (Fernet), only to send. Erasure nulls
the encrypted copy; the hash cannot be reversed. Screens see ``+57 *** *** 4567``.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import MISSING, BaseModel, SecretStr

MarketingConsent = Literal["granted", "revoked", "none"]
ObjectionTag = Literal[
    "price", "shipping", "size", "availability", "trust", "timing", "payment", "off_topic", "other"
]


def normalize_number(wa_id: str) -> str:
    """Digits only, as WhatsApp sends ``wa_id``: "+57 300-000-0001" → "573000000001"."""
    digits = re.sub(r"\D", "", wa_id)
    if not 8 <= len(digits) <= 15:
        raise ValueError("a WhatsApp number has 8 to 15 digits (E.164)")
    return digits


def mask_phone(number: str) -> str:
    digits = normalize_number(number)
    country = digits[:-10] if len(digits) > 10 else ""
    return f"+{country} *** *** {digits[-4:]}" if country else f"*** *** {digits[-4:]}"


@dataclass(frozen=True)
class Sealed:
    hash: bytes
    encrypted: bytes


class ContactIdentity:
    def __init__(self, *, pepper: SecretStr, key: SecretStr) -> None:
        self.pepper = pepper
        self.key = key
        self._fernet = Fernet(key.get_secret_value().encode())

    @classmethod
    def from_settings(cls, *, pepper: SecretStr | None, key: SecretStr | None) -> Self:
        if pepper is None or key is None:
            raise LookupError(
                "contacts need CONTACT_HASH_PEPPER and CONTACT_ENCRYPTION_KEY (.env.example)"
            )
        return cls(pepper=pepper, key=key)

    def hash(self, wa_id: str) -> bytes:
        number = normalize_number(wa_id).encode()
        return hmac.new(self.pepper.get_secret_value().encode(), number, hashlib.sha256).digest()

    def seal(self, wa_id: str) -> Sealed:
        return Sealed(self.hash(wa_id), self._fernet.encrypt(normalize_number(wa_id).encode()))

    def open(self, encrypted: bytes) -> str:
        return self._fernet.decrypt(encrypted).decode()


class Contact(BaseModel):
    """openapi.yaml ``Contact``; objections and conversation counts belong to conversation."""

    id: UUID
    seller_id: UUID
    display_name: str | None = None
    phone_masked: str | MISSING = MISSING
    marketing_consent: MarketingConsent
    opted_out_at: datetime | None = None
    erased_at: datetime | None = None
    last_objection_tag: ObjectionTag | None = None
    last_marketing_sent_at: datetime | None = None
    conversation_count: int | MISSING = MISSING
    created_at: datetime
