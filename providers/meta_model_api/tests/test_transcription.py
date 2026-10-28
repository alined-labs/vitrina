import io
import json
import math
import struct
import sys
import wave

import httpx2
import pytest

from vitrina_core.providers import Blob, ProviderError, RateLimitedError, TranscriptionRequest
from vitrina_core.settings import Settings
from vitrina_meta_model_api import MetaTranscriber, meta_client
from vitrina_meta_model_api.testing import FakeApi, error

WAV = Blob(data=b"RIFF....WAVEfmt ", mime_type="audio/wav")


def transcript(text: str, duration_ms: int = 2400) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={"sessionId": "s-1", "transcript": text, "audioDurationMs": duration_ms, "turns": []},
    )


def parts(request: httpx2.Request) -> dict[str, tuple[str | None, bytes]]:
    """The multipart parts by name: (content type, payload)."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    found: dict[str, tuple[str | None, bytes]] = {}
    for chunk in request.content.split(b"--" + boundary)[1:-1]:
        head, _, payload = chunk.strip(b"\r\n").partition(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        name = lines[0].split('name="')[1].split('"')[0]
        kind = next(
            (line.split(": ")[1] for line in lines if line.startswith("Content-Type")), None
        )
        found[name] = (kind, payload)
    return found


async def test_as_3_6_a_voice_note_goes_as_multipart_wav_with_a_spanish_bias(
    api: FakeApi,
) -> None:
    api.answer(transcript("  ¿Tienen la mochila azul en talla grande?  "))

    response = await MetaTranscriber(api.client()).transcribe(
        TranscriptionRequest(model="transcribe-from-settings", audio=WAV)
    )

    sent = api.requests[0]
    assert sent.url == "https://meta.test/v1/asr/transcribe"
    found = parts(sent)
    assert found["request"][0] == "application/json"
    assert json.loads(found["request"][1]) == {
        "model": "transcribe-from-settings",
        "audioEncoding": "WAV",
        "mode": "PUSH_TO_TALK",
        "languageBias": ["Spanish"],
    }
    assert found["audio"] == ("audio/wav", WAV.data)
    assert response.text == "¿Tienen la mochila azul en talla grande?"
    assert (response.language, response.duration_s) == ("es", 2.4)
    assert (response.meta.model, response.meta.purpose) == (
        "transcribe-from-settings",
        "transcribe",
    )


async def test_without_a_known_language_the_model_detects_it(api: FakeApi) -> None:
    api.answer(transcript("hola"))

    await MetaTranscriber(api.client()).transcribe(
        TranscriptionRequest(model="m", audio=WAV, language=None)
    )

    assert "languageBias" not in json.loads(parts(api.requests[0])["request"][1])


async def test_an_answer_without_a_transcript_is_a_provider_error(api: FakeApi) -> None:
    api.answer(httpx2.Response(200, json={"sessionId": "s-1"}))

    with pytest.raises(ProviderError):
        await MetaTranscriber(api.client()).transcribe(TranscriptionRequest(model="m", audio=WAV))


async def test_as_3_11_transcription_limits_are_retryable(api: FakeApi) -> None:
    api.answer(error(429, "concurrency limit"))

    with pytest.raises(RateLimitedError):
        await MetaTranscriber(api.client()).transcribe(TranscriptionRequest(model="m", audio=WAV))


@pytest.mark.live
async def test_live_transcribe_answers_a_short_wav() -> None:
    """Q02 needs a spoken Spanish clip; this only proves the request shape with a tone:

    uv run --env-file .env pytest -m live providers/meta_model_api/tests/test_transcription.py
    """
    settings = Settings()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)))
                for i in range(16000)
            )
        )

    response = await MetaTranscriber(meta_client(settings)).transcribe(
        TranscriptionRequest(
            model=settings.model_id("transcribe"),
            audio=Blob(data=buffer.getvalue(), mime_type="audio/wav"),
        )
    )

    assert response.duration_s is not None
    sys.stdout.write(f"\ntranscribe: {response.text!r}, {response.duration_s} s, {response.meta}\n")
