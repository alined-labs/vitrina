"""T051 voice notes as text (AS-3.6): download → 16 kHz mono WAV → Muse Voice Transcribe.

Whether Transcribe takes OGG/Opus and Spanish is open (Q02); the default transcodes with
ffmpeg first and, without it, sends the audio as it came. A fake ``ffmpeg`` script checks the
command line and the file handling without the real binary (not installed on the 8 GB
machines, plan.md §3).
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from vitrina_conversation.voice import Ffmpeg, TranscodeError, VoiceNotes, ffmpeg_on_path
from vitrina_core.providers import (
    Blob,
    CallMeta,
    ProviderError,
    TranscriptionRequest,
    TranscriptionResponse,
)

OGG = Blob(data=b"OggS\x00\x02voice-note", mime_type="audio/ogg; codecs=opus")
WAV = Blob(data=b"RIFF\x24\x00\x00\x00WAVEfmt ", mime_type="audio/wav")


@dataclass
class FakeTranscriber:
    text: str = " ¿Cuánto vale la mochila azul? "
    requests: list[TranscriptionRequest] = field(default_factory=list)

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        self.requests.append(request)
        meta = CallMeta(provider="local_mock", model=request.model, purpose=request.purpose)
        return TranscriptionResponse(text=self.text, language="es", meta=meta)


@dataclass
class FakeTranscoder:
    failure: TranscodeError | None = None
    received: list[Blob] = field(default_factory=list)

    async def to_wav(self, audio: Blob) -> Blob:
        self.received.append(audio)
        if self.failure is not None:
            raise self.failure
        return WAV


async def download(media_id: str) -> Blob:
    if media_id != "1000000000000102":
        raise ProviderError("unknown media id", provider="whatsapp")
    return OGG


def voice(transcoder: FakeTranscoder | None, transcriber: FakeTranscriber) -> VoiceNotes:
    return VoiceNotes(
        download=download,
        transcriber=transcriber,
        model="transcribe-test",
        transcoder=transcoder,
    )


async def test_as_3_6_a_voice_note_becomes_text_through_wav() -> None:
    transcoder, transcriber = FakeTranscoder(), FakeTranscriber()

    text = await voice(transcoder, transcriber).transcribe("1000000000000102")

    assert text == "¿Cuánto vale la mochila azul?"
    assert transcoder.received == [OGG]
    (request,) = transcriber.requests
    assert (request.model, request.audio, request.language) == ("transcribe-test", WAV, "es")


async def test_q02_without_ffmpeg_the_audio_goes_as_it_came() -> None:
    transcriber = FakeTranscriber()

    await voice(None, transcriber).transcribe("1000000000000102")

    assert transcriber.requests[0].audio == OGG


async def test_art_viii_a_failed_transcode_sends_the_audio_as_it_came() -> None:
    transcoder = FakeTranscoder(failure=TranscodeError("ffmpeg exited with 1"))
    transcriber = FakeTranscriber()

    await voice(transcoder, transcriber).transcribe("1000000000000102")

    assert transcriber.requests[0].audio == OGG


async def test_art_viii_a_download_failure_reaches_the_caller() -> None:
    with pytest.raises(ProviderError):
        await voice(None, FakeTranscriber()).transcribe("1000000000000999")


# ── the ffmpeg runner, against a fake binary ──

FAKE_FFMPEG = """\
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
Path(sys.argv[0] + ".args").write_text(json.dumps(args))
Path(sys.argv[0] + ".pid").write_text(str(os.getpid()))
mode = Path(sys.argv[0] + ".mode").read_text() if Path(sys.argv[0] + ".mode").exists() else ""
if mode == "fail":
    sys.stderr.write("Invalid data found when processing input\\n")
    sys.exit(1)
if mode == "slow":
    time.sleep(5)
if mode == "silent":
    sys.exit(0)
source = Path(args[args.index("-i") + 1])
Path(args[-1]).write_bytes(b"RIFF" + source.read_bytes())
if mode == "partial":
    sys.exit(1)
"""


@dataclass(frozen=True)
class FakeFfmpeg:
    binary: Path

    def behave(self, mode: str) -> None:
        Path(f"{self.binary}.mode").write_text(mode)

    def args(self) -> list[str]:
        loaded: list[str] = json.loads(Path(f"{self.binary}.args").read_text())
        return loaded

    def pid(self) -> int | None:
        """The running fake's process id, once it has started."""
        written = Path(f"{self.binary}.pid")
        return int(written.read_text()) if written.exists() and written.read_text() else None

    def runner(self, timeout_s: float = 10) -> Ffmpeg:
        return Ffmpeg(str(self.binary), timeout_s=timeout_s)


@pytest.fixture
def fake_ffmpeg(tmp_path: Path) -> FakeFfmpeg:
    binary = tmp_path / "bin" / "ffmpeg"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n{FAKE_FFMPEG}")
    binary.chmod(0o755)
    return FakeFfmpeg(binary)


async def test_q02_ffmpeg_makes_16_khz_mono_wav(fake_ffmpeg: FakeFfmpeg) -> None:
    wav = await fake_ffmpeg.runner().to_wav(OGG)

    assert (wav.mime_type, wav.data) == ("audio/wav", b"RIFF" + OGG.data)
    args = fake_ffmpeg.args()
    assert args[args.index("-ac") + 1] == "1"
    assert args[args.index("-ar") + 1] == "16000"
    assert args[args.index("-c:a") + 1] == "pcm_s16le"
    assert "-nostdin" in args
    assert args[-1].endswith(".wav")


@pytest.mark.parametrize("mode", ["fail", "silent", "partial"])
async def test_q02_an_ffmpeg_failure_is_a_transcode_error(
    fake_ffmpeg: FakeFfmpeg, mode: str
) -> None:
    fake_ffmpeg.behave(mode)

    with pytest.raises(TranscodeError):
        await fake_ffmpeg.runner().to_wav(OGG)


async def test_ffmpeg_reads_only_a_local_file_with_an_audio_demuxer(
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    await fake_ffmpeg.runner().to_wav(OGG)

    args = fake_ffmpeg.args()
    source = args.index("-i")
    protocols, formats = args.index("-protocol_whitelist"), args.index("-format_whitelist")
    assert max(protocols, formats) < source
    assert args[protocols + 1] == "file"
    assert {"ogg", "mp3", "aac", "amr", "mov"} <= set(args[formats + 1].split(","))
    assert not {"hls", "concat", "sdp"} & set(args[formats + 1].split(","))


async def test_a_cancelled_transcode_stops_ffmpeg(fake_ffmpeg: FakeFfmpeg) -> None:
    fake_ffmpeg.behave("slow")
    task = asyncio.create_task(fake_ffmpeg.runner(timeout_s=30).to_wav(OGG))
    pid = None
    for _ in range(100):
        if (pid := fake_ffmpeg.pid()) is not None:
            break
        await asyncio.sleep(0.05)
    assert pid is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(60):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    pytest.fail("ffmpeg kept running after the transcode was cancelled")


async def test_art_viii_1_a_stuck_ffmpeg_is_stopped(fake_ffmpeg: FakeFfmpeg) -> None:
    fake_ffmpeg.behave("slow")

    with pytest.raises(TranscodeError):
        await fake_ffmpeg.runner(timeout_s=0.5).to_wav(OGG)


def test_q02_ffmpeg_is_used_when_it_is_on_the_path(
    fake_ffmpeg: FakeFfmpeg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(fake_ffmpeg.binary.parent))
    assert ffmpeg_on_path(timeout_s=7) == fake_ffmpeg.runner(timeout_s=7)

    monkeypatch.setenv("PATH", os.devnull)
    assert ffmpeg_on_path(timeout_s=7) is None
