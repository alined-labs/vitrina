from collections.abc import Iterator

import pytest

from vitrina_meta_model_api.testing import FakeApi


@pytest.fixture
def api() -> Iterator[FakeApi]:
    fake = FakeApi()
    yield fake
    assert not fake.answers, "a queued answer was never requested"
