import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from vitrina_core.http import install_problem_handlers
from vitrina_core.providers import Blob
from vitrina_core.settings import Settings
from vitrina_core.storage import (
    BlobNotFoundError,
    LocalBlobStore,
    StorageError,
    StorageUnavailableError,
    SupabaseBlobStore,
    blob_key,
    blob_store,
    create_blob_router,
    seller_of,
)

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
ASSET = UUID("00000000-0000-7000-8000-0000000000a1")
KEY = f"{SELLER}/assets/{ASSET}.jpg"
PHOTO = b"\xff\xd8\xff fake jpeg bytes"
NOW = 1793116800.0


def test_art_v_3_blob_keys_start_with_the_seller() -> None:
    assert blob_key(SELLER, "assets", ASSET, "jpg") == KEY
    assert blob_key(SELLER, "creatives", f"{ASSET}/feed_1x1", "png") == (
        f"{SELLER}/creatives/{ASSET}/feed_1x1.png"
    )
    assert seller_of(KEY) == SELLER


@pytest.mark.parametrize(
    "key",
    [
        "",
        "assets/photo.jpg",
        f"/{SELLER}/assets/x.jpg",
        f"{SELLER}/assets/../../other/x.jpg",
        f"{SELLER}/assets//x.jpg",
        f"{SELLER}/Assets/x.jpg",
        f"{SELLER}/assets/x y.jpg",
        f"{SELLER}/assets",
        f"not-a-uuid/assets/{ASSET}.jpg",
    ],
)
def test_keys_outside_the_seller_scheme_are_refused(key: str) -> None:
    with pytest.raises(ValueError, match="blob key"):
        seller_of(key)


def test_blob_key_refuses_unsafe_parts() -> None:
    with pytest.raises(ValueError, match="blob key"):
        blob_key(SELLER, "assets", "../x", "jpg")
    with pytest.raises(ValueError, match="blob key"):
        blob_key(SELLER, "assets", ASSET, "j/pg")


# ── local files ──


def local(tmp_path: Path, clock: Callable[[], float] = lambda: NOW) -> LocalBlobStore:
    return LocalBlobStore(
        tmp_path, public_url="http://localhost:8000", secret=b"s" * 32, clock=clock
    )


async def test_local_store_keeps_bytes_and_content_type(tmp_path: Path) -> None:
    store = local(tmp_path)

    await store.put(KEY, PHOTO, "image/jpeg")
    await store.put(KEY, PHOTO + b"!", "image/jpeg")  # same key: replaced

    assert await store.get(KEY) == Blob(data=PHOTO + b"!", mime_type="image/jpeg")


async def test_local_delete_is_idempotent_and_get_then_misses(tmp_path: Path) -> None:
    store = local(tmp_path)
    await store.put(KEY, PHOTO, "image/jpeg")

    await store.delete(KEY)
    await store.delete(KEY)

    with pytest.raises(BlobNotFoundError):
        await store.get(KEY)
    with pytest.raises(BlobNotFoundError):
        await store.signed_url(KEY)


def served(store: LocalBlobStore) -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(create_blob_router(store))
    return TestClient(app)


async def test_a_local_signed_url_serves_the_blob(tmp_path: Path) -> None:
    store = local(tmp_path)
    await store.put(KEY, PHOTO, "image/jpeg")

    url = await store.signed_url(KEY, ttl_s=600)
    response = served(store).get(url)

    assert url.startswith(f"http://localhost:8000/local-blobs/{KEY}?")
    assert response.status_code == 200
    assert response.content == PHOTO
    assert response.headers["content-type"] == "image/jpeg"


async def test_a_tampered_or_reused_local_url_is_forbidden(tmp_path: Path) -> None:
    store = local(tmp_path)
    other = blob_key(SELLER, "assets", UUID(int=ASSET.int + 1), "jpg")
    await store.put(KEY, PHOTO, "image/jpeg")
    await store.put(other, PHOTO, "image/jpeg")
    url = await store.signed_url(KEY, ttl_s=600)
    query = parse_qs(urlsplit(url).query)
    client = served(store)

    later = client.get(url.replace(f"expires={query['expires'][0]}", "expires=4102444800"))
    elsewhere = client.get(url.replace(KEY, other))
    unsigned = client.get(f"/local-blobs/{KEY}")

    assert later.status_code == 403
    assert later.headers["content-type"] == "application/problem+json"
    assert elsewhere.status_code == 403
    assert unsigned.status_code == 403


async def test_a_local_url_expires_after_its_ttl(tmp_path: Path) -> None:
    now = [NOW]
    store = local(tmp_path, clock=lambda: now[0])
    await store.put(KEY, PHOTO, "image/jpeg")
    url = await store.signed_url(KEY, ttl_s=60)
    client = served(store)

    assert client.get(url).status_code == 200
    now[0] += 61
    assert client.get(url).status_code == 403


async def test_a_signed_url_for_a_deleted_blob_answers_not_found(tmp_path: Path) -> None:
    store = local(tmp_path)
    await store.put(KEY, PHOTO, "image/jpeg")
    url = await store.signed_url(KEY)
    await store.delete(KEY)

    response = served(store).get(url)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


# ── Supabase Storage ──


class FakeStorage:
    """Supabase Storage behind an httpx2 mock (paths as storage-js sends them)."""

    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def store(self) -> tuple[SupabaseBlobStore, list[float]]:
        waits: list[float] = []

        async def sleep(seconds: float) -> None:
            waits.append(seconds)

        store = SupabaseBlobStore(
            "https://project.supabase.test",
            SecretStr("service-role-key"),
            bucket="vitrina",
            client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)),
            sleep=sleep,
        )
        return store, waits


BASE = "https://project.supabase.test/storage/v1"


async def test_supabase_put_uploads_with_upsert_and_the_service_key() -> None:
    fake = FakeStorage(httpx2.Response(200, json={"Key": f"vitrina/{KEY}"}))
    store, _ = fake.store()

    await store.put(KEY, PHOTO, "image/jpeg")

    [sent] = fake.requests
    assert (sent.method, str(sent.url)) == ("POST", f"{BASE}/object/vitrina/{KEY}")
    assert sent.headers["authorization"] == "Bearer service-role-key"
    assert sent.headers["apikey"] == "service-role-key"
    assert sent.headers["content-type"] == "image/jpeg"
    assert sent.headers["x-upsert"] == "true"
    assert sent.content == PHOTO


async def test_supabase_get_returns_the_blob_with_its_content_type() -> None:
    fake = FakeStorage(httpx2.Response(200, content=PHOTO, headers={"content-type": "image/jpeg"}))
    store, _ = fake.store()

    blob = await store.get(KEY)

    assert (fake.requests[0].method, str(fake.requests[0].url)) == (
        "GET",
        f"{BASE}/object/vitrina/{KEY}",
    )
    assert blob == Blob(data=PHOTO, mime_type="image/jpeg")


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(404, json={"statusCode": "404", "error": "not_found", "message": "x"}),
        # Older Storage versions answer 400 with the 404 in the body.
        httpx2.Response(400, json={"statusCode": "404", "error": "not_found", "message": "x"}),
    ],
    ids=["404", "400 with a 404 body"],
)
async def test_supabase_missing_objects_are_not_found(answer: httpx2.Response) -> None:
    store, _ = FakeStorage(answer).store()

    with pytest.raises(BlobNotFoundError):
        await store.get(KEY)


async def test_supabase_signed_url_is_absolute_and_asks_for_the_ttl() -> None:
    signed = f"/object/sign/vitrina/{KEY}?token=signed-token"
    fake = FakeStorage(httpx2.Response(200, json={"signedURL": signed}))
    store, _ = fake.store()

    url = await store.signed_url(KEY, ttl_s=600)

    [sent] = fake.requests
    assert (sent.method, str(sent.url)) == ("POST", f"{BASE}/object/sign/vitrina/{KEY}")
    assert json.loads(sent.content) == {"expiresIn": 600}
    assert url == f"{BASE}{signed}"


async def test_supabase_delete_removes_the_key_by_prefix() -> None:
    fake = FakeStorage(httpx2.Response(200, json=[]))
    store, _ = fake.store()

    await store.delete(KEY)

    [sent] = fake.requests
    assert (sent.method, str(sent.url)) == ("DELETE", f"{BASE}/object/vitrina")
    assert json.loads(sent.content) == {"prefixes": [KEY]}


async def test_art_viii_1_supabase_outages_are_retried_with_backoff() -> None:
    fake = FakeStorage(
        httpx2.Response(503, json={"message": "unavailable"}),
        httpx2.ReadTimeout("read timed out"),
        httpx2.Response(200, content=PHOTO, headers={"content-type": "image/jpeg"}),
    )
    store, waits = fake.store()

    blob = await store.get(KEY)

    assert blob.data == PHOTO
    assert waits == [0.5, 1.0]


async def test_supabase_gives_up_after_three_attempts() -> None:
    fake = FakeStorage(*(httpx2.Response(502, json={"message": "bad gateway"}) for _ in range(3)))
    store, _ = fake.store()

    with pytest.raises(StorageUnavailableError):
        await store.get(KEY)

    assert len(fake.requests) == 3


async def test_supabase_refusals_are_final() -> None:
    fake = FakeStorage(httpx2.Response(403, json={"statusCode": "403", "message": "denied"}))
    store, _ = fake.store()

    with pytest.raises(StorageError, match="denied") as caught:
        await store.put(KEY, PHOTO, "image/jpeg")

    assert not caught.value.retryable
    assert len(fake.requests) == 1


async def test_the_store_refuses_keys_outside_the_seller_scheme(tmp_path: Path) -> None:
    store, _ = FakeStorage().store()

    with pytest.raises(ValueError, match="blob key"):
        await store.put("../escape.jpg", PHOTO, "image/jpeg")
    with pytest.raises(ValueError, match="blob key"):
        await local(tmp_path).put("../escape.jpg", PHOTO, "image/jpeg")


# ── from settings ──


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_settings_choose_local_files_by_default(tmp_path: Path) -> None:
    store = blob_store(settings(storage_dir=tmp_path))

    assert isinstance(store, LocalBlobStore)
    assert store.root == tmp_path


def test_supabase_storage_needs_the_project_url_and_service_key() -> None:
    with pytest.raises(LookupError, match="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"):
        blob_store(settings(storage_backend="supabase"))

    store = blob_store(
        settings(
            storage_backend="supabase",
            supabase_url="https://project.supabase.test",
            supabase_service_role_key="service-role-key",
            storage_bucket="vitrina-dev",
        )
    )

    assert isinstance(store, SupabaseBlobStore)
    assert store.bucket == "vitrina-dev"


def test_storage_outages_answer_503_like_a_model_provider() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/upload")
    async def upload() -> None:
        raise StorageUnavailableError("Supabase Storage timed out")

    response = TestClient(app).get("/upload")

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
