import pytest

from vitrina_core.settings import Settings
from vitrina_worker.main import serve


async def test_the_worker_refuses_to_start_without_a_database() -> None:
    with pytest.raises(SystemExit, match="needs DATABASE_URL and DB_SCHEMA"):
        await serve(Settings(_env_file=None))  # type: ignore[call-arg]
