"""Voice notes as text (T051, AS-3.6): download → 16 kHz mono WAV → Muse Voice Transcribe.

WhatsApp voice notes are OGG/Opus. Whether Transcribe takes that, and Spanish, is still open
(Q02), so the default stands: transcode to 16 kHz mono WAV with ffmpeg first
(plan.md §1). Without ffmpeg on the PATH, or when it fails, the audio goes as it came, and a
failed transcription reaches the pipeline, which tells the customer it could not hear them
(Art. VIII). Files go through a temporary folder, because an M4A from an iPhone needs a
seekable input and a WAV header needs a seekable output.
"""

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vitrina_core.providers import Blob, Transcriber, TranscriptionRequest

logger = logging.getLogger(__name__)

LANGUAGE = "es"  # es-CO only (NFR-08)
WAV_ARGS = ("-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le")
"""Audio only, mono, 16 kHz, 16-bit PCM."""

MediaDownload = Callable[[str], Awaitable[Blob]]
"""``media_id -> audio`` (``WhatsAppClient.download_media``)."""


class Voice(Protocol):
    """Voice notes to text, for the pipeline."""

    async def transcribe(self, media_id: str) -> str: ...


class TranscodeError(Exception):
    """ffmpeg could not turn the audio into WAV; the message never carries the audio."""


class Transcoder(Protocol):
    async def to_wav(self, audio: Blob) -> Blob: ...


@dataclass(frozen=True)
class Ffmpeg:
    binary: str
    timeout_s: float

    async def to_wav(self, audio: Blob) -> Blob:
        with tempfile.TemporaryDirectory(prefix="vitrina-voice-") as folder:
            source, target = Path(folder) / "note", Path(folder) / "note.wav"
            await asyncio.to_thread(source.write_bytes, audio.data)
            process = await asyncio.create_subprocess_exec(
                self.binary,
                *("-hide_banner", "-loglevel", "error", "-nostdin", "-y"),
                *("-i", str(source)),
                *WAV_ARGS,
                str(target),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(self.timeout_s):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
                raise TranscodeError(f"ffmpeg took more than {self.timeout_s:g} s") from None
            if process.returncode != 0 or not target.is_file():
                raise TranscodeError(f"ffmpeg exited with {process.returncode} and no WAV")
            return Blob(data=await asyncio.to_thread(target.read_bytes), mime_type="audio/wav")


def ffmpeg_on_path(*, timeout_s: float) -> Ffmpeg | None:
    binary = shutil.which("ffmpeg")
    return Ffmpeg(binary, timeout_s=timeout_s) if binary else None


@dataclass(frozen=True)
class VoiceNotes:
    download: MediaDownload
    transcriber: Transcriber
    model: str
    """``MODEL_TRANSCRIBE`` (``settings.model_id("transcribe")``); never a literal (Q01)."""
    transcoder: Transcoder | None = None

    async def transcribe(self, media_id: str) -> str:
        """The voice note's words; ``ProviderError`` or ``TimeoutError`` when it cannot be heard."""
        audio = await self.download(media_id)
        if self.transcoder is not None:
            try:
                audio = await self.transcoder.to_wav(audio)
            except TranscodeError as error:
                logger.warning("voice note sent as it came: %s", error)
        response = await self.transcriber.transcribe(
            TranscriptionRequest(model=self.model, audio=audio, language=LANGUAGE)
        )
        return response.text.strip()
