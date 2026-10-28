"""Time-ordered UUIDv7 ids (RFC 9562), so cursors can page on ``(created_at, id)``.

``seeded_ids(seed)`` makes ``new_id`` repeatable inside a block. Evals replay recorded model
calls (AS-7.3), and an id a tool hands back to the model is part of the next request, so a
dry-run turn must mint the same ids on every run.
"""

import itertools
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

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


SEEDED_NAMESPACE = UUID("0192f2e7-9c41-7b5a-8d03-5e7f1a2b4c69")
_seeded: ContextVar[Callable[[], UUID] | None] = ContextVar("vitrina_seeded_ids", default=None)


def new_id() -> UUID:
    seeded = _seeded.get()
    return seeded() if seeded is not None else uuid7()


@contextmanager
def seeded_ids(seed: str) -> Iterator[None]:
    """Inside the block ``new_id`` returns ``uuid5(seed, n)`` for n = 0, 1, … in call order."""
    counter = itertools.count()
    token = _seeded.set(lambda: uuid5(SEEDED_NAMESPACE, f"{seed}:{next(counter)}"))
    try:
        yield
    finally:
        _seeded.reset(token)
