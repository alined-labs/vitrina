"""Who and which request is being served: for problem bodies, logs and the ledger (FR-084).

``RequestIdMiddleware`` scopes both values to one request; ``core.auth`` sets the seller and
the worker sets it per job.
"""

from contextvars import ContextVar
from uuid import UUID

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
seller_id_var: ContextVar[UUID | None] = ContextVar("seller_id", default=None)


def current_request_id() -> str | None:
    return request_id_var.get()


def current_seller_id() -> UUID | None:
    return seller_id_var.get()
