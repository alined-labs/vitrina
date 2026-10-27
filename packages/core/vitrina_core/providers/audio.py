"""Voice-note transcription (Muse Voice Transcribe)."""

from typing import Protocol

from vitrina_core.providers.base import Blob, CallMeta, Frozen, ModelPurpose


class TranscriptionRequest(Frozen):
    model: str
    purpose: ModelPurpose = "transcribe"
    audio: Blob
    language: str | None = "es"


class TranscriptionResponse(Frozen):
    text: str
    language: str | None = None
    duration_s: float | None = None
    meta: CallMeta


class Transcriber(Protocol):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse: ...
