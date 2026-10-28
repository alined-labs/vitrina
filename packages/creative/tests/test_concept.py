import pytest

from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderError,
)
from vitrina_creative.concept import FALLBACK, clean, sam_concept


class Spark:
    def __init__(self, content: str | None = None, *, fail: bool = False) -> None:
        self.content, self.fail = content, fail
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if self.fail:
            raise ProviderError("refused", provider="meta_model_api", status_code=400)
        return ChatResponse(
            message=ChatMessage(role="assistant", content=self.content),
            finish_reason="stop",
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose=request.purpose),
        )


async def test_as_2_1_spark_names_the_spanish_product_in_english_for_sam() -> None:
    spark = Spark('{"noun": "woven bag"}')

    noun = await sam_concept("Mochila wayuu", "Tejida a mano", chat=spark, model="spark")

    assert noun == "woven bag"
    request = spark.requests[0]
    assert (request.model, request.purpose, request.temperature) == ("spark", "segment", 0.0)
    assert request.response_format is not None
    assert request.messages[-1].content == "Name: Mochila wayuu\nDescription: Tejida a mano"


@pytest.mark.parametrize(
    ("content", "noun"),
    [
        ('{"noun": "T-Shirt"}', "t-shirt"),
        ('{"noun": "a red woven shoulder bag."}', "a red woven"),
        ("handbag", "handbag"),
        ('{"noun": ""}', FALLBACK),
        ('{"noun": "mochila 🎒 #1"}', "mochila"),
        ("[]", FALLBACK),
    ],
)
def test_the_answer_is_cut_to_a_few_plain_words(content: str, noun: str) -> None:
    assert clean(content) == noun


async def test_a_refused_call_falls_back_to_a_generic_noun() -> None:
    assert await sam_concept("Arepa", "", chat=Spark(fail=True), model="spark") == FALLBACK
