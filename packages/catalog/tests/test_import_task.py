from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from vitrina_catalog.tasks import import_products
from vitrina_core.jobs import ClaimedJob, JobContext, JobFailedError
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import compose
from vitrina_core.settings import Settings
from vitrina_core.storage import LocalBlobStore, blob_key

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")


class Queue:
    async def progress(self, job_id: UUID, stage: str, progress: float) -> None:
        return None


def context(tmp_path: Path, image_key: str, **settings: Any) -> JobContext:
    config = Settings(_env_file=None, cassette_dir=tmp_path, **settings)
    job = ClaimedJob(
        id=UUID(int=1),
        kind="product_import",
        task="catalog.import_products",
        payload={"image_key": image_key},
        owner_seller_id=SELLER,
        attempt=1,
        max_attempts=3,
    )
    return JobContext(
        job,
        cast(Any, Queue()),
        sessions=cast(Any, object()),  # never reached: each case fails before the database
        providers=compose(config, InMemoryLedger()),
        storage=LocalBlobStore(tmp_path, public_url="http://localhost:8000"),
        settings=config,
    )


async def test_art_v_3_a_photo_stored_for_another_seller_is_refused(tmp_path: Path) -> None:
    key = blob_key(OTHER, "imports", "menu", "png")

    with pytest.raises(JobFailedError) as caught:
        await import_products(context(tmp_path, key, model_agent="m"))

    assert caught.value.code == "VALIDATION_ERROR"


async def test_a_job_without_a_valid_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(JobFailedError) as caught:
        await import_products(context(tmp_path, "../menu.png", model_agent="m"))

    assert caught.value.code == "VALIDATION_ERROR"


async def test_an_unset_model_id_fails_the_job_for_good(tmp_path: Path) -> None:
    key = blob_key(SELLER, "imports", "menu", "png")

    with pytest.raises(JobFailedError) as caught:
        await import_products(context(tmp_path, key))

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert "MODEL_AGENT" in caught.value.message


async def test_a_photo_that_is_gone_fails_with_not_found(tmp_path: Path) -> None:
    key = blob_key(SELLER, "imports", "menu", "png")

    with pytest.raises(JobFailedError) as caught:
        await import_products(context(tmp_path, key, model_agent="m"))

    assert caught.value.code == "NOT_FOUND"
