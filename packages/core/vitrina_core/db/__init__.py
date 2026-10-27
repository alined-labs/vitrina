"""Database access: async engine, sessions, seller-scoped repositories (T005).

Each module declares the tables it owns on ``metadata`` in its own package; the schema
itself comes from the Alembic migrations in ``migrations/``.
"""

import sqlalchemy as sa

from vitrina_core.db.engine import create_engine, create_session_factory, search_path, session_scope
from vitrina_core.db.repository import SellerScopedRepository

metadata = sa.MetaData()

__all__ = [
    "SellerScopedRepository",
    "create_engine",
    "create_session_factory",
    "metadata",
    "search_path",
    "session_scope",
]
