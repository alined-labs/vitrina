"""Public interface of the catalog module (constitution Art. VI.3).

The conversation engine's tools call these methods (agent-tools.json ``search_catalog``,
``get_product``, ``check_stock``, ``reserve_item``); every call is scoped to the seller the
engine resolved, never to ids the model supplies. Only ``active`` products and facts are
visible (R1, FR-003). ``InMemoryCatalog`` implements the protocol for tests and local work;
the Postgres implementation with hybrid search replaces it in T020 behind the same protocol.

The engine adds what other modules own: ``approved_ad_ids`` (creative.api) and the
``ad.reservation`` attribution event after a successful ``reserve``.
"""

from vitrina_catalog.domain import (
    RESERVATION_TTL,
    Catalog,
    Citation,
    FactKind,
    FactStatus,
    ProductDetail,
    ProductNotFoundError,
    ProductStatus,
    Reservation,
    SearchKind,
    SearchResult,
    StockLevel,
)
from vitrina_catalog.memory import InMemoryCatalog

__all__ = [
    "RESERVATION_TTL",
    "Catalog",
    "Citation",
    "FactKind",
    "FactStatus",
    "InMemoryCatalog",
    "ProductDetail",
    "ProductNotFoundError",
    "ProductStatus",
    "Reservation",
    "SearchKind",
    "SearchResult",
    "StockLevel",
]
