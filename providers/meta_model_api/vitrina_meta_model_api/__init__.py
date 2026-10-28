"""Meta Model API adapter (OpenAI-compatible): chat, embeddings, image, segmentation,
transcription (plan.md §1, ADR-0003). Chat and embeddings: T011; image and SAM: T031–T033.

``MetaChatModel``, ``MetaEmbedder``, ``MetaImageModel``, ``MetaSegmenter`` and ``MetaTranscriber`` share one client from ``meta_client``;
``live_providers`` hands them to ``vitrina_core.providers.compose``. Model IDs come from
settings (``MODEL_*``), never from code (Q01).
"""

from vitrina_meta_model_api.chat import MetaChatModel
from vitrina_meta_model_api.client import meta_client
from vitrina_meta_model_api.embeddings import MetaEmbedder
from vitrina_meta_model_api.images import MetaImageModel
from vitrina_meta_model_api.live import live_providers
from vitrina_meta_model_api.segmentation import MetaSegmenter
from vitrina_meta_model_api.transcription import MetaTranscriber

__all__ = [
    "MetaChatModel",
    "MetaEmbedder",
    "MetaImageModel",
    "MetaSegmenter",
    "MetaTranscriber",
    "live_providers",
    "meta_client",
]
