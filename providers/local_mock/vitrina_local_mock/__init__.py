"""Local mocks for zero-network runs: the WhatsApp messenger with a read-only HTML view
(plan.md §4.6, constitution Art. VIII.3; T010).
"""

from vitrina_local_mock.messenger import LocalMockMessenger, MockEntry, MockOutbox
from vitrina_local_mock.phone import SYNTHETIC_NUMBER, create_phone_router

__all__ = [
    "SYNTHETIC_NUMBER",
    "LocalMockMessenger",
    "MockEntry",
    "MockOutbox",
    "create_phone_router",
]
