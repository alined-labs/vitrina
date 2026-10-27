"""Text and image embeddings for hybrid retrieval and photo matching (data-model.md products)."""

from typing import Literal, Protocol, Self

from pydantic import model_validator

from vitrina_core.providers.base import Blob, CallMeta, Frozen, ModelPurpose


class EmbeddingRequest(Frozen):
    model: str
    purpose: ModelPurpose = "embed"
    texts: tuple[str, ...] = ()
    images: tuple[Blob, ...] = ()
    dimensions: int | None = None

    @model_validator(mode="after")
    def _one_kind_of_input(self) -> Self:
        if bool(self.texts) == bool(self.images):
            raise ValueError("an embedding request takes exactly one of texts or images")
        return self

    @property
    def kind(self) -> Literal["text", "image"]:
        return "text" if self.texts else "image"


class EmbeddingResponse(Frozen):
    vectors: tuple[tuple[float, ...], ...]
    meta: CallMeta


class Embedder(Protocol):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...
