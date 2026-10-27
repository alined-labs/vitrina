"""Meta Model API adapter (OpenAI-compatible): chat, embeddings, image, segmentation,
transcription (plan.md §1, ADR-0003). Owners: MX0 (chat, embeddings; T011), MX1 (image, SAM).

``MetaChatModel`` and ``MetaEmbedder`` share one client from ``meta_client``;
``live_providers`` hands them to ``vitrina_core.providers.compose``. Model IDs come from
settings (``MODEL_*``), never from code (research/OPEN.md Q01).
"""

from vitrina_meta_model_api.chat import MetaChatModel
from vitrina_meta_model_api.client import meta_client
from vitrina_meta_model_api.embeddings import MetaEmbedder
from vitrina_meta_model_api.live import live_providers

__all__ = ["MetaChatModel", "MetaEmbedder", "live_providers", "meta_client"]
