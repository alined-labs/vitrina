"""Recorded cassettes and replay mode (plan.md §4.5, ADR-0010, AS-7.3).

Each interaction is one JSON file, ``<root>/<kind>/<sha256>.json``, keyed by a hash of the
canonical request, so machines add cassettes without merge conflicts. ``record`` calls the
live provider and stores the response; ``replay`` answers from the file and never touches
the network. Binary inputs are hashed into the key and summarized in the file.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from vitrina_core.providers.audio import Transcriber, TranscriptionRequest, TranscriptionResponse
from vitrina_core.providers.chat import ChatModel, ChatRequest, ChatResponse
from vitrina_core.providers.embeddings import Embedder, EmbeddingRequest, EmbeddingResponse
from vitrina_core.providers.errors import CassetteMissError
from vitrina_core.providers.images import (
    ImageModel,
    ImageRequest,
    ImageResponse,
    Segmenter,
    SegmentRequest,
    SegmentResponse,
)


class CassetteMode(StrEnum):
    replay = "replay"
    record = "record"


def _summarize(value: Any) -> Any:
    """The request as stored in a cassette: blobs become their hash, size and type."""
    if isinstance(value, dict):
        if set(value) == {"data", "mime_type"} and isinstance(value["data"], bytes):
            data: bytes = value["data"]
            return {
                "mime_type": value["mime_type"],
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        return {key: _summarize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_summarize(item) for item in value]
    return value


class Cassettes:
    def __init__(self, root: Path, mode: CassetteMode) -> None:
        self.root = root
        self.mode = mode

    def key(self, kind: str, request: BaseModel) -> str:
        canonical = json.dumps(
            {"kind": kind, "request": _summarize(request.model_dump(mode="python"))},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def path(self, kind: str, request: BaseModel) -> Path:
        return self.root / kind / f"{self.key(kind, request)}.json"

    async def run[ResponseT: BaseModel](
        self,
        kind: str,
        request: BaseModel,
        live: Callable[[], Awaitable[ResponseT]] | None,
        response_type: type[ResponseT],
    ) -> ResponseT:
        path = self.path(kind, request)
        if self.mode is CassetteMode.replay:
            if not await asyncio.to_thread(path.exists):
                raise CassetteMissError(
                    f"no cassette for {kind} {path.stem[:12]} under {self.root}; record it with the live provider"
                )
            stored = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            response = response_type.model_validate_json(json.dumps(stored["response"]))
            meta = getattr(response, "meta", None)
            if isinstance(meta, BaseModel):
                response = response.model_copy(
                    update={"meta": meta.model_copy(update={"replayed": True})}
                )
            return response
        if live is None:
            raise ValueError("record mode needs a live provider")
        response = await live()
        document = {
            "kind": kind,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": _summarize(request.model_dump(mode="python")),
            "response": json.loads(response.model_dump_json()),
        }
        text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
        return response


class _Recorded:
    def __init__(self, cassettes: Cassettes, live: object | None) -> None:
        if cassettes.mode is CassetteMode.record and live is None:
            raise ValueError(f"{type(self).__name__} in record mode needs a live provider")
        self._cassettes = cassettes


class RecordedChatModel(_Recorded):
    def __init__(self, cassettes: Cassettes, live: ChatModel | None = None) -> None:
        super().__init__(cassettes, live)
        self._live = live

    async def complete(self, request: ChatRequest) -> ChatResponse:
        live = self._live
        call = (lambda: live.complete(request)) if live else None
        return await self._cassettes.run("chat", request, call, ChatResponse)


class RecordedEmbedder(_Recorded):
    def __init__(self, cassettes: Cassettes, live: Embedder | None = None) -> None:
        super().__init__(cassettes, live)
        self._live = live

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        live = self._live
        call = (lambda: live.embed(request)) if live else None
        return await self._cassettes.run("embed", request, call, EmbeddingResponse)


class RecordedImageModel(_Recorded):
    def __init__(self, cassettes: Cassettes, live: ImageModel | None = None) -> None:
        super().__init__(cassettes, live)
        self._live = live

    async def generate(self, request: ImageRequest) -> ImageResponse:
        live = self._live
        call = (lambda: live.generate(request)) if live else None
        return await self._cassettes.run("image", request, call, ImageResponse)


class RecordedSegmenter(_Recorded):
    def __init__(self, cassettes: Cassettes, live: Segmenter | None = None) -> None:
        super().__init__(cassettes, live)
        self._live = live

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        live = self._live
        call = (lambda: live.segment(request)) if live else None
        return await self._cassettes.run("segment", request, call, SegmentResponse)


class RecordedTranscriber(_Recorded):
    def __init__(self, cassettes: Cassettes, live: Transcriber | None = None) -> None:
        super().__init__(cassettes, live)
        self._live = live

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        live = self._live
        call = (lambda: live.transcribe(request)) if live else None
        return await self._cassettes.run("transcribe", request, call, TranscriptionResponse)
