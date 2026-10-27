"""WhatsApp Cloud API settings (FR-020, FR-021, FR-028).

The variable names are the ones in ``.env.example``. Once ``core.settings`` lands (T005) it
builds this object and ``from_env`` remains for tools and live tests.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self

GRAPH_BASE_URL = "https://graph.facebook.com"
# Newest Graph API version on 2026-10-27: v26.0, released 2026-07-29, no expiry announced
# (developers.facebook.com/docs/graph-api/changelog/versions). Override: WHATSAPP_GRAPH_VERSION.
DEFAULT_GRAPH_VERSION = "v26.0"


@dataclass(frozen=True, slots=True)
class WhatsAppConfig:
    phone_number_id: str
    app_secret: str = field(repr=False)
    verify_token: str = field(repr=False)
    access_token: str = field(repr=False)
    graph_version: str = DEFAULT_GRAPH_VERSION

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        env = os.environ if env is None else env
        return cls(
            phone_number_id=env.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            app_secret=env.get("WHATSAPP_APP_SECRET", ""),
            verify_token=env.get("WHATSAPP_VERIFY_TOKEN", ""),
            access_token=env.get("WHATSAPP_ACCESS_TOKEN", ""),
            graph_version=env.get("WHATSAPP_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION,
        )

    @property
    def can_receive(self) -> bool:
        """Webhooks can be verified: handshake token and app secret are set (FR-020, FR-021)."""
        return bool(self.verify_token and self.app_secret)

    @property
    def can_send(self) -> bool:
        """Outbound sends are possible: access token and phone number ID are set (FR-028)."""
        return bool(self.access_token and self.phone_number_id)
