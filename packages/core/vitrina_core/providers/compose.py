"""The providers a process uses, chosen once from settings (ADR-0010, AS-7.3).

``replay`` answers from cassettes and never builds a live adapter, so it needs no key;
``record`` calls the live adapters and stores each answer as a cassette; ``live`` calls
them directly. Every call, replayed or not, reaches the model-call ledger (FR-084).
Callers still wrap each call in ``with_backoff``.

    providers = compose(settings, ledger, lambda: live_providers(settings))
    response = await with_backoff(lambda: providers.chat.complete(request))

``live_providers`` lives in ``providers/meta_model_api``. A role with no live adapter yet
keeps replaying in record mode, and in live mode fails on the call, not at startup.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from vitrina_core.ledger import (
    LedgeredChatModel,
    LedgeredEmbedder,
    LedgeredImageModel,
    LedgeredSegmenter,
    LedgeredTranscriber,
    ModelCallLedger,
)
from vitrina_core.providers.audio import Transcriber, TranscriptionRequest, TranscriptionResponse
from vitrina_core.providers.chat import ChatModel, ChatRequest, ChatResponse
from vitrina_core.providers.embeddings import Embedder, EmbeddingRequest, EmbeddingResponse
from vitrina_core.providers.errors import ProviderError
from vitrina_core.providers.images import (
    ImageModel,
    ImageRequest,
    ImageResponse,
    Segmenter,
    SegmentRequest,
    SegmentResponse,
)
from vitrina_core.providers.recorded import (
    CassetteMode,
    Cassettes,
    RecordedChatModel,
    RecordedEmbedder,
    RecordedImageModel,
    RecordedSegmenter,
    RecordedTranscriber,
)
from vitrina_core.settings import Settings


class ProviderMode(StrEnum):
    replay = "replay"
    record = "record"
    live = "live"

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """REPLAY=true always replays, so it protects the credit; RECORD_CASSETTES applies
        with REPLAY=false (.env.example)."""
        if settings.replay:
            return cls.replay
        return cls.record if settings.record_cassettes else cls.live


@dataclass(frozen=True)
class LiveProviders:
    """The live adapters from ``providers/*``; None marks a role with no adapter yet."""

    chat: ChatModel | None = None
    embedder: Embedder | None = None
    image: ImageModel | None = None
    segmenter: Segmenter | None = None
    transcriber: Transcriber | None = None


@dataclass(frozen=True)
class Providers:
    mode: ProviderMode
    chat: ChatModel
    embedder: Embedder
    image: ImageModel
    segmenter: Segmenter
    transcriber: Transcriber


class NoLiveAdapter:
    """Stands in for a role with no live adapter in live mode: each call fails, final."""

    def __init__(self, role: str) -> None:
        self.role = role

    def _error(self) -> ProviderError:
        return ProviderError(
            f"no live {self.role} adapter in providers/meta_model_api yet; use REPLAY=true",
            provider="meta_model_api",
        )

    async def complete(self, request: ChatRequest) -> ChatResponse:
        raise self._error()

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        raise self._error()

    async def generate(self, request: ImageRequest) -> ImageResponse:
        raise self._error()

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        raise self._error()

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        raise self._error()


def compose(
    settings: Settings,
    ledger: ModelCallLedger,
    live: Callable[[], LiveProviders] | None = None,
    *,
    mode: ProviderMode | None = None,
) -> Providers:
    """``live`` builds the adapters and runs only outside replay mode (it may need keys)."""
    mode = mode or ProviderMode.from_settings(settings)
    adapters = live() if live is not None and mode is not ProviderMode.replay else LiveProviders()
    replay = Cassettes(settings.cassette_dir, CassetteMode.replay)
    record = Cassettes(settings.cassette_dir, CassetteMode.record)

    def choose[T](adapter: T | None, recorded: Callable[[Cassettes, T | None], T], missing: T) -> T:
        if adapter is None:
            return missing if mode is ProviderMode.live else recorded(replay, None)
        return recorded(record, adapter) if mode is ProviderMode.record else adapter

    return Providers(
        mode=mode,
        chat=LedgeredChatModel(
            choose(adapters.chat, RecordedChatModel, NoLiveAdapter("chat")), ledger
        ),
        embedder=LedgeredEmbedder(
            choose(adapters.embedder, RecordedEmbedder, NoLiveAdapter("embedding")), ledger
        ),
        image=LedgeredImageModel(
            choose(adapters.image, RecordedImageModel, NoLiveAdapter("image")), ledger
        ),
        segmenter=LedgeredSegmenter(
            choose(adapters.segmenter, RecordedSegmenter, NoLiveAdapter("segmentation")), ledger
        ),
        transcriber=LedgeredTranscriber(
            choose(adapters.transcriber, RecordedTranscriber, NoLiveAdapter("transcription")),
            ledger,
        ),
    )
