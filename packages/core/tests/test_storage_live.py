"""Live round trip on Supabase Storage. Needs credentials; run by hand:

    uv run --env-file .env pytest -m live packages/core/tests/test_storage_live.py

STORAGE_BUCKET must exist as a private bucket. The check writes one small text object under
a random seller id and deletes it again.
"""

import httpx2
import pytest

from vitrina_core.ids import new_id
from vitrina_core.settings import Settings
from vitrina_core.storage import BlobNotFoundError, SupabaseBlobStore, blob_key

pytestmark = pytest.mark.live


async def test_live_supabase_storage_puts_signs_gets_and_deletes() -> None:
    settings = Settings()
    if not settings.supabase_url or settings.supabase_service_role_key is None:
        pytest.skip("needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
    store = SupabaseBlobStore(
        settings.supabase_url, settings.supabase_service_role_key, bucket=settings.storage_bucket
    )
    key = blob_key(new_id(), "live_checks", new_id(), "txt")
    try:
        await store.put(key, b"vitrina", "text/plain")
        assert (await store.get(key)).data == b"vitrina"
        async with httpx2.AsyncClient() as client:
            response = await client.get(await store.signed_url(key, ttl_s=60))
        assert response.content == b"vitrina"
    finally:
        await store.delete(key)
    with pytest.raises(BlobNotFoundError):
        await store.get(key)
    await store.aclose()
