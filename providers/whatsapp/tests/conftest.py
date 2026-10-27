from collections.abc import Callable
from pathlib import Path

import pytest

WEBHOOK_FIXTURES = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "webhooks"


@pytest.fixture
def webhook_fixtures() -> Path:
    """Recorded webhook payloads shared with replay mode (evals/fixtures/README.md)."""
    return WEBHOOK_FIXTURES


@pytest.fixture
def webhook_body() -> Callable[[str], bytes]:
    """Raw bytes of a recorded payload, exactly as they would arrive on the wire."""

    def load(name: str) -> bytes:
        return (WEBHOOK_FIXTURES / f"{name}.json").read_bytes()

    return load
