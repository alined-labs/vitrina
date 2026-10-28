"""The live Meta Model API adapters for ``vitrina_core.providers.compose``."""

from vitrina_core.providers.compose import LiveProviders
from vitrina_core.settings import Settings
from vitrina_meta_model_api.chat import MetaChatModel
from vitrina_meta_model_api.client import meta_client
from vitrina_meta_model_api.embeddings import MetaEmbedder
from vitrina_meta_model_api.images import MetaImageModel
from vitrina_meta_model_api.segmentation import MetaSegmenter
from vitrina_meta_model_api.transcription import MetaTranscriber


def live_providers(settings: Settings, *, chat_timeout_s: float | None = None) -> LiveProviders:
    """Needs META_API_KEY; every role the Meta Model API serves.

    ``chat_timeout_s`` replaces PROVIDER_TIMEOUT_S for chat calls only: the worker passes
    PROVIDER_JOB_TIMEOUT_S, since its copy, judge and brief calls are background jobs.
    """
    client = meta_client(settings)
    chat = client if chat_timeout_s is None else client.with_options(timeout=chat_timeout_s)
    return LiveProviders(
        chat=MetaChatModel(chat),
        embedder=MetaEmbedder(client),
        image=MetaImageModel(client),
        segmenter=MetaSegmenter(client),
        transcriber=MetaTranscriber(client),
    )
