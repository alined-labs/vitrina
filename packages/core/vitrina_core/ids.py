"""Time-ordered UUIDv7 ids (RFC 9562), so cursors can page on ``(created_at, id)``."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

NIL = UUID(int=0)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def uuid7(at: datetime | None = None) -> UUID:
    """48-bit Unix milliseconds, version 7, 74 random bits."""
    moment = at or datetime.now(UTC)
    milliseconds = ((moment - EPOCH) // timedelta(milliseconds=1)) & ((1 << 48) - 1)
    random = int.from_bytes(os.urandom(10))
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | ((random >> 62) & 0xFFF) << 64
        | (0b10 << 62)
        | (random & ((1 << 62) - 1))
    )
    return UUID(int=value)


def uuid7_time(value: UUID) -> datetime:
    return EPOCH + timedelta(milliseconds=value.int >> 80)


def new_id() -> UUID:
    return uuid7()
