"""Voice notes as text (Muse Voice Transcribe) for T051 (AS-3.6).

``POST /asr/transcribe`` takes multipart form data: a JSON ``request`` part and the ``audio``
part. Only WAV is documented (mono PCM at 16 or 24 kHz, at most 10 minutes and 32 MB), which is
what ``vitrina_conversation.voice`` converts WhatsApp's OGG/Opus into. The language is a bias by
name, not a code; without one the model detects it.
"""

import json
from typing import Any

from openai import AsyncOpenAI

from vitrina_core.providers import (
    CallMeta,
    ProviderError,
    ProviderName,
    TranscriptionRequest,
    TranscriptionResponse,
)
from vitrina_meta_model_api.client import call

LANGUAGE_NAMES = {"es": "Spanish", "en": "English"}


class MetaTranscriber:
    """``Transcriber`` on the Meta Model API's ASR endpoint."""

    def __init__(self, client: AsyncOpenAI, *, provider: ProviderName = "meta_model_api") -> None:
        self.client = client
        self.provider = provider

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        settings: dict[str, Any] = {
            "model": request.model,
            "audioEncoding": "WAV",
            "mode": "PUSH_TO_TALK",
        }
        if (name := LANGUAGE_NAMES.get(request.language or "")) is not None:
            settings["languageBias"] = [name]
        files: list[tuple[str, tuple[str | None, bytes, str]]] = [
            ("request", (None, json.dumps(settings).encode(), "application/json")),
            ("audio", ("voice.wav", request.audio.data, request.audio.mime_type)),
        ]
        answer, latency_ms = await call(
            self.provider,
            lambda: self.client.post(
                "/asr/transcribe",
                files=files,
                cast_to=object,
                # As in the SDK's own uploads: the client then sets the boundary.
                options={"headers": {"Content-Type": "multipart/form-data"}},
            ),
        )
        if not isinstance(answer, dict) or not isinstance(answer.get("transcript"), str):
            raise ProviderError(
                f"{self.provider} answered a transcription without a transcript",
                provider=self.provider,
            )
        duration_ms = answer.get("audioDurationMs")
        return TranscriptionResponse(
            text=answer["transcript"].strip(),
            language=request.language,
            duration_s=duration_ms / 1000 if isinstance(duration_ms, int) else None,
            meta=CallMeta(
                provider=self.provider,
                model=request.model,
                purpose=request.purpose,
                latency_ms=latency_ms,
            ),
        )
