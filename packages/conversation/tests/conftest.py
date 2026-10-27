import pytest

from vitrina_conversation.testing import Shop, seeded_shop


@pytest.fixture
def shop() -> Shop:
    return seeded_shop()
