"""Text embeddings for hybrid catalog search (data-model.md ``products.embedding``)."""

from typing import Any

from openai import AsyncOpenAI
from openai.types import CreateEmbeddingResponse

from vitrina_core.providers import (
    CallMeta,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderError,
    ProviderName,
    ProviderUnavailableError,
)
from vitrina_meta_model_api.client import call, tokens


class MetaEmbedder:
    """``Embedder`` on an OpenAI-compatible ``/embeddings``. Which model, and whether images
    embed at all, is research/OPEN.md Q06: image requests fail until it is answered.
    """

    def __init__(self, client: AsyncOpenAI, *, provider: ProviderName = "meta_model_api") -> None:
        self.client = client
        self.provider = provider

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if request.kind == "image":
            raise ProviderError(
                f"{self.provider} has no image embedding endpoint yet (research/OPEN.md Q06)",
                provider=self.provider,
            )
        # Plain floats: compatible endpoints need not support base64 vectors.
        body: dict[str, Any] = {
            "model": request.model,
            "input": list(request.texts),
            "encoding_format": "float",
        }
        if request.dimensions is not None:
            body["dimensions"] = request.dimensions
        response, latency_ms = await call(
            self.provider,
            lambda: self.client.post("/embeddings", body=body, cast_to=CreateEmbeddingResponse),
        )
        data = sorted(response.data or (), key=lambda item: item.index)
        if len(data) != len(request.texts):
            raise ProviderUnavailableError(
                f"{self.provider} returned {len(data)} vectors for {len(request.texts)} texts",
                provider=self.provider,
            )
        return EmbeddingResponse(
            vectors=tuple(tuple(item.embedding) for item in data),
            meta=CallMeta(
                provider=self.provider,
                model=request.model,
                purpose=request.purpose,
                tokens_in=tokens(getattr(response, "usage", None), "prompt_tokens"),
                latency_ms=latency_ms,
            ),
        )
