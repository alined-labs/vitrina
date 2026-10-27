"""Blob storage for assets, creatives, masks, exports and inbound media (I-15, plan.md §1).

Keys are seller-scoped, ``<seller_id>/<kind>/<name>.<ext>`` (Art. V.3): build them with
``blob_key``; every store refuses any other shape. ``blob_store(settings)`` picks one:

- ``LocalBlobStore`` keeps files under STORAGE_DIR, for tests, replay and machines without
  Supabase (B8). Its signed URLs point at a hidden API route, ``create_blob_router``, and
  are signed with a per-process secret, so they die with the process.
- ``SupabaseBlobStore`` uses one private Supabase Storage bucket (STORAGE_BUCKET) with the
  service-role key; each call has a timeout and backoff on 429 and 5xx (Art. VIII.1).

Signed URLs live SIGNED_URL_TTL_S seconds unless the caller asks for another TTL.
"""

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx2
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import SecretStr

from vitrina_core.http.problems import ProblemCode, ProblemError
from vitrina_core.providers.base import Blob
from vitrina_core.providers.errors import ProviderError
from vitrina_core.providers.retry import with_backoff
from vitrina_core.settings import Settings

LOCAL_ROUTE = "/local-blobs"
REASON_MAX = 300

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_KEY = re.compile(
    r"(?P<seller>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    rf"/[a-z][a-z0-9_]*(?:/{_SEGMENT})+"
)
_EXTENSION = re.compile(r"[a-z0-9]+")


class StorageError(ProviderError):
    """A storage call failed. Like a model provider's: the API answers 503
    PROVIDER_UNAVAILABLE, and the worker retries a job when ``retryable``."""

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message, provider="storage", retryable=retryable, status_code=status_code)


class StorageUnavailableError(StorageError):
    """Timeout, lost connection, 429 or 5xx."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message, retryable=True, status_code=status_code)


class BlobNotFoundError(LookupError):
    """No blob at this key: a 404 for the caller to answer, never an outage."""

    def __init__(self, key: str) -> None:
        super().__init__(f"no blob at {key}")
        self.key = key


def seller_of(key: str) -> UUID:
    """The seller a key belongs to; ValueError for anything outside the key scheme."""
    match = _KEY.fullmatch(key)
    if match is None:
        raise ValueError(f"invalid blob key {key!r}: expected <seller_id>/<kind>/<name>.<ext>")
    return UUID(match["seller"])


def blob_key(seller_id: UUID, kind: str, name: str | UUID, ext: str) -> str:
    """``<seller_id>/<kind>/<name>.<ext>``; ``name`` may hold ``/`` for nested keys."""
    if not _EXTENSION.fullmatch(ext):
        raise ValueError(f"invalid blob key extension {ext!r}")
    key = f"{seller_id}/{kind}/{name}.{ext}"
    seller_of(key)
    return key


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> Blob: ...

    async def signed_url(self, key: str, ttl_s: int | None = None) -> str: ...

    async def delete(self, key: str) -> None: ...


# ── local files ──


class LocalBlobStore:
    """Objects in ``<root>/objects/<key>``, their content types in ``<root>/types/<key>``."""

    def __init__(
        self,
        root: Path,
        *,
        public_url: str,
        secret: bytes | None = None,
        default_ttl_s: int = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root
        self.public_url = public_url.rstrip("/")
        self.default_ttl_s = default_ttl_s
        self.clock = clock
        self._secret = secret or secrets.token_bytes(32)

    def _paths(self, key: str) -> tuple[Path, Path]:
        seller_of(key)
        return self.root / "objects" / key, self.root / "types" / key

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        data_path, type_path = self._paths(key)
        await asyncio.to_thread(_replace, type_path, content_type.encode())
        await asyncio.to_thread(_replace, data_path, data)

    async def get(self, key: str) -> Blob:
        data_path, type_path = self._paths(key)
        try:
            data = await asyncio.to_thread(data_path.read_bytes)
            mime_type = await asyncio.to_thread(type_path.read_text, encoding="utf-8")
        except FileNotFoundError as error:
            raise BlobNotFoundError(key) from error
        return Blob(data=data, mime_type=mime_type)

    async def signed_url(self, key: str, ttl_s: int | None = None) -> str:
        data_path, _ = self._paths(key)
        if not await asyncio.to_thread(data_path.is_file):
            raise BlobNotFoundError(key)
        expires = int(self.clock()) + (ttl_s or self.default_ttl_s)
        token = self._token(key, expires)
        return f"{self.public_url}{LOCAL_ROUTE}/{key}?expires={expires}&token={token}"

    async def delete(self, key: str) -> None:
        for path in self._paths(key):
            await asyncio.to_thread(path.unlink, missing_ok=True)

    async def aclose(self) -> None:
        return None

    def verify(self, key: str, expires: int, token: str) -> bool:
        return expires > self.clock() and hmac.compare_digest(token, self._token(key, expires))

    def _token(self, key: str, expires: int) -> str:
        return hmac.new(self._secret, f"{key}\n{expires}".encode(), hashlib.sha256).hexdigest()


def _replace(path: Path, data: bytes) -> None:
    """Write through a temporary file, so a reader never sees half a blob."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(4)}.partial")
    partial.write_bytes(data)
    os.replace(partial, path)


def create_blob_router(store: LocalBlobStore) -> APIRouter:
    """Serves local signed URLs; outside the contract and hidden from OpenAPI."""
    router = APIRouter(include_in_schema=False)

    @router.get(LOCAL_ROUTE + "/{key:path}")
    async def local_blob(key: str, expires: int = 0, token: str = "") -> Response:
        if not store.verify(key, expires, token):
            raise ProblemError(ProblemCode.FORBIDDEN, detail="this link is invalid or expired")
        try:
            blob = await store.get(key)
        except (BlobNotFoundError, ValueError) as error:
            raise ProblemError(
                ProblemCode.NOT_FOUND, detail="this file no longer exists"
            ) from error
        remaining = max(0, expires - int(store.clock()))
        return Response(
            blob.data,
            media_type=blob.mime_type,
            headers={"cache-control": f"private, max-age={remaining}"},
        )

    return router


# ── Supabase Storage ──


class SupabaseBlobStore:
    """One private bucket, called with the service-role key (the paths storage-js uses)."""

    def __init__(
        self,
        url: str,
        service_key: SecretStr,
        *,
        bucket: str,
        default_ttl_s: int = 900,
        timeout_s: float = 20.0,
        client: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base = f"{url.rstrip('/')}/storage/v1"
        self.bucket = bucket
        self.default_ttl_s = default_ttl_s
        secret = service_key.get_secret_value()
        self._headers = {"authorization": f"Bearer {secret}", "apikey": secret}
        self._client = client or httpx2.AsyncClient(timeout=timeout_s)
        self._sleep = sleep

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._send(
            "POST",
            f"/object/{self.bucket}/{key}",
            key,
            content=data,
            headers={"content-type": content_type, "x-upsert": "true"},
        )

    async def get(self, key: str) -> Blob:
        response = await self._send("GET", f"/object/{self.bucket}/{key}", key)
        mime_type = response.headers.get("content-type", "application/octet-stream")
        return Blob(data=response.content, mime_type=mime_type)

    async def signed_url(self, key: str, ttl_s: int | None = None) -> str:
        response = await self._send(
            "POST",
            f"/object/sign/{self.bucket}/{key}",
            key,
            json={"expiresIn": ttl_s or self.default_ttl_s},
        )
        signed = _body(response).get("signedURL")
        if not isinstance(signed, str):
            raise StorageError("Supabase Storage signed no URL")
        return f"{self.base}{signed}"

    async def delete(self, key: str) -> None:
        await self._send("DELETE", f"/object/{self.bucket}", key, json={"prefixes": [key]})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, key: str, **options: Any) -> httpx2.Response:
        seller_of(key)
        headers = {**self._headers, **options.pop("headers", {})}

        async def attempt() -> httpx2.Response:
            try:
                response = await self._client.request(
                    method, self.base + path, headers=headers, **options
                )
            except httpx2.TimeoutException as error:
                raise StorageUnavailableError("Supabase Storage timed out") from error
            except httpx2.TransportError as error:
                raise StorageUnavailableError(
                    f"Supabase Storage is unreachable: {error}"
                ) from error
            if response.is_success:
                return response
            raise _error(response, key, self.bucket)

        return await with_backoff(attempt, sleep=self._sleep)


def _body(response: httpx2.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error(response: httpx2.Response, key: str, bucket: str) -> StorageError | BlobNotFoundError:
    body = _body(response)
    status = response.status_code
    reason = " ".join(str(body.get("message") or body.get("error") or response.text).split())
    message = f"Supabase Storage answered {status}: {reason[:REASON_MAX]}"
    # Older Storage versions answer 400 with the 404 in the body.
    if status == 404 or str(body.get("statusCode")) == "404":
        if "bucket" in f"{body.get('error')} {body.get('message')}".lower():
            return StorageError(
                f"{message} (create the private bucket {bucket!r})", status_code=status
            )
        return BlobNotFoundError(key)
    if status == 429 or status >= 500:
        return StorageUnavailableError(message, status_code=status)
    return StorageError(message, status_code=status)


def blob_store(settings: Settings) -> LocalBlobStore | SupabaseBlobStore:
    if settings.storage_backend == "local":
        return LocalBlobStore(
            settings.storage_dir,
            public_url=settings.public_api_url,
            default_ttl_s=settings.signed_url_ttl_s,
        )
    if not settings.supabase_url or settings.supabase_service_role_key is None:
        raise LookupError(
            "STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
        )
    return SupabaseBlobStore(
        settings.supabase_url,
        settings.supabase_service_role_key,
        bucket=settings.storage_bucket,
        default_ttl_s=settings.signed_url_ttl_s,
        timeout_s=settings.provider_timeout_s,
    )
