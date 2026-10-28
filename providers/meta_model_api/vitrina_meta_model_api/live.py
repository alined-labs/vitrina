"""The live Meta Model API adapters for ``vitrina_core.providers.compose``."""

from vitrina_core.providers.compose import LiveProviders
from vitrina_core.settings import Settings
from vitrina_meta_model_api.chat import MetaChatModel
from vitrina_meta_model_api.client import meta_client
from vitrina_meta_model_api.embeddings import MetaEmbedder
from vitrina_meta_model_api.images import MetaImageModel


def live_providers(settings: Settings) -> LiveProviders:
    """Needs META_API_KEY. Segmentation joins here when MX1 adds it."""
    client = meta_client(settings)
    return LiveProviders(
        chat=MetaChatModel(client), embedder=MetaEmbedder(client), image=MetaImageModel(client)
    )
