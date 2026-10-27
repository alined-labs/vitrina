"""Cursor pagination on ``(created_at, id)`` (FR-080; openapi.yaml ``Limit``, ``Cursor``, ``Page``).

Cursors are opaque to clients: base64url JSON of the last item's ``created_at`` and ``id``.
UUIDv7 ids keep the order stable even when two rows share a timestamp.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import Query
from pydantic import BaseModel

from vitrina_core.db.repository import AnySelect
from vitrina_core.http.problems import FieldError, ProblemCode, ProblemError

MAX_LIMIT = 100
DEFAULT_LIMIT = 25


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class PageQuery:
    """``limit`` and ``cursor`` query parameters; use as ``Annotated[PageQuery, Depends()]``."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        cursor: Annotated[str | None, Query()] = None,
    ) -> None:
        self.limit = limit
        self.cursor = cursor


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, row_id = json.loads(raw)
        return datetime.fromisoformat(created_at), UUID(row_id)
    except (binascii.Error, ValueError, TypeError) as error:
        raise ProblemError(
            ProblemCode.VALIDATION_ERROR,
            detail="cursor is not one this API issued",
            errors=[FieldError(field="cursor", message="invalid cursor")],
        ) from error


def keyset_page(
    statement: AnySelect,
    table: sa.Table,
    *,
    limit: int,
    cursor: str | None,
    newest_first: bool = True,
) -> AnySelect:
    """Order by ``(created_at, id)``, start after ``cursor``, fetch one extra row to detect more."""
    created_at, row_id = table.c.created_at, table.c.id
    if cursor is not None:
        after = sa.tuple_(created_at, row_id)
        position = sa.tuple_(*decode_cursor(cursor))
        statement = statement.where(after < position if newest_first else after > position)
    order = (created_at.desc(), row_id.desc()) if newest_first else (created_at.asc(), row_id.asc())
    return statement.order_by(*order).limit(limit + 1)


def next_cursor[R: Mapping[str, Any]](
    rows: Sequence[R], *, limit: int
) -> tuple[list[R], str | None]:
    """Split the ``limit + 1`` rows of ``keyset_page`` into the page and its ``next_cursor``."""
    items = list(rows[:limit])
    if len(rows) <= limit:
        return items, None
    last = items[-1]
    return items, encode_cursor(last["created_at"], last["id"])
