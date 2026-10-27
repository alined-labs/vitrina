import pytest

from vitrina_core.providers import Blob, EmbeddingRequest, ProviderError, RateLimitedError
from vitrina_meta_model_api import MetaEmbedder
from vitrina_meta_model_api.testing import FakeApi, embeddings, error


async def test_embeddings_send_the_texts_and_return_vectors_in_input_order(api: FakeApi) -> None:
    api.answer(embeddings([0.1, 0.2], [0.3, 0.4], prompt_tokens=9))
    request = EmbeddingRequest(
        model="embedder-from-settings", texts=("mochila azul", "envíos a Medellín"), dimensions=2
    )

    response = await MetaEmbedder(api.client()).embed(request)

    assert api.requests[0].url == "https://meta.test/v1/embeddings"
    assert api.body() == {
        "model": "embedder-from-settings",
        "input": ["mochila azul", "envíos a Medellín"],
        "dimensions": 2,
        # Plain floats: compatible endpoints need not support base64 vectors.
        "encoding_format": "float",
    }
    assert response.vectors == ((0.1, 0.2), (0.3, 0.4))
    meta = response.meta
    assert (meta.provider, meta.model, meta.purpose) == (
        "meta_model_api",
        "embedder-from-settings",
        "embed",
    )
    assert (meta.tokens_in, meta.tokens_out, meta.cost_usd) == (9, 0, None)


async def test_embeddings_leave_dimensions_to_the_model_when_unset(api: FakeApi) -> None:
    api.answer(embeddings([0.5]))

    await MetaEmbedder(api.client()).embed(EmbeddingRequest(model="e", texts=("hola",)))

    assert "dimensions" not in api.body()


async def test_as_3_11_embedding_rate_limits_are_retryable(api: FakeApi) -> None:
    api.answer(error(429, "slow down", **{"retry-after": "2"}))

    with pytest.raises(RateLimitedError) as caught:
        await MetaEmbedder(api.client()).embed(EmbeddingRequest(model="e", texts=("hola",)))

    assert caught.value.retry_after_s == 2.0


async def test_image_embeddings_fail_without_a_request_until_q06_names_a_model(
    api: FakeApi,
) -> None:
    request = EmbeddingRequest(model="e", images=(Blob(data=b"jpeg", mime_type="image/jpeg"),))

    with pytest.raises(ProviderError) as caught:
        await MetaEmbedder(api.client()).embed(request)

    assert not caught.value.retryable
    assert "Q06" in str(caught.value)
    assert api.requests == []
