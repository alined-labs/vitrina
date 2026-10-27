"""How the inbound pipeline is put together (T027, T051).

Voice notes are heard only with the Cloud API, which can download their audio, and with
``MODEL_TRANSCRIBE`` set (Q01: model IDs are configuration); otherwise they get the text-only
note.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vitrina_conversation.pacing import Pacer
from vitrina_conversation.voice import VoiceNotes
from vitrina_conversation.wiring import postgres_pipeline, voice_notes
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import ProviderMode, Providers, compose
from vitrina_core.settings import Settings
from vitrina_local_mock import LocalMockMessenger, MockOutbox
from vitrina_whatsapp.client import WhatsAppClient


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def providers(config: Settings) -> Providers:
    return compose(config, InMemoryLedger(), mode=ProviderMode.replay)


def cloud_api() -> WhatsAppClient:
    return WhatsAppClient(access_token="wiring-test-token", phone_number_id="100000000000001")


async def test_as_3_6_voice_notes_are_heard_through_the_cloud_api() -> None:
    config = settings(model_transcribe="transcribe-test")
    async with cloud_api() as client:
        voice = voice_notes(config, providers(config), client)

        assert isinstance(voice, VoiceNotes)
        assert (voice.model, voice.download) == ("transcribe-test", client.download_media)


async def test_q01_without_model_transcribe_voice_notes_are_not_heard() -> None:
    config = settings()
    async with cloud_api() as client:
        assert voice_notes(config, providers(config), client) is None


def test_fr_022_the_local_mock_has_no_voice_notes_to_download(tmp_path: Path) -> None:
    config = settings(model_transcribe="transcribe-test")
    mock = LocalMockMessenger(MockOutbox(tmp_path / "mock-whatsapp.jsonl"))

    assert voice_notes(config, providers(config), mock) is None


async def test_as_3_6_the_worker_pipeline_hears_voice_notes() -> None:
    config = settings(model_agent="spark-test", model_transcribe="transcribe-test")
    engine = create_async_engine("postgresql+asyncpg://vitrina@localhost/never-connected")
    try:
        async with cloud_api() as client:
            deps = postgres_pipeline(
                settings=config,
                sessions=async_sessionmaker(engine),
                providers=providers(config),
                messenger=client,
                pacer=Pacer(),
            )

            assert isinstance(deps.voice, VoiceNotes)
    finally:
        await engine.dispose()
