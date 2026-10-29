"""HTTP conventions every router shares (T007): problem+json errors (FR-082), request ids
(FR-084), cursor pagination (FR-080), idempotent POSTs (FR-081) and request body limits.
"""

from vitrina_core.http.body_limit import BodyLimitMiddleware
from vitrina_core.http.context import current_request_id
from vitrina_core.http.idempotency import (
    IdempotencyKey,
    IdempotencyMiddleware,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PostgresIdempotencyStore,
    idempotency_keys,
)
from vitrina_core.http.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Page,
    PageQuery,
    decode_cursor,
    encode_cursor,
    keyset_page,
    next_cursor,
)
from vitrina_core.http.problems import (
    CATALOGUE,
    PROBLEM_JSON,
    FieldError,
    Problem,
    ProblemCode,
    ProblemError,
    install_problem_handlers,
    problem_responses,
)
from vitrina_core.http.request_id import RequestIdMiddleware

__all__ = [
    "CATALOGUE",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PROBLEM_JSON",
    "BodyLimitMiddleware",
    "FieldError",
    "IdempotencyKey",
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "Page",
    "PageQuery",
    "PostgresIdempotencyStore",
    "Problem",
    "ProblemCode",
    "ProblemError",
    "RequestIdMiddleware",
    "current_request_id",
    "decode_cursor",
    "encode_cursor",
    "idempotency_keys",
    "install_problem_handlers",
    "keyset_page",
    "next_cursor",
    "problem_responses",
]
