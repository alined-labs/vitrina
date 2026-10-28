"""T050 customer photos (AS-3.5): what the photo shows, in words the catalog can match.

Spark (vision) describes the main product in one short Spanish sentence; the catalog is then
searched with that description, products only. SAM cropping waits on a live segmenter and
image embeddings on an embedding model (Q01, Q06), so the match is by description alone.
"""

from dataclasses import dataclass, field

import pytest

from vitrina_conversation.image import NO_PRODUCT, Photos
from vitrina_conversation.testing import ScriptedChat, Shop
from vitrina_core.providers import Blob, ImagePart, ProviderError, TextPart

PHOTO = Blob(data=b"\xff\xd8\xff\xe0customer-photo", mime_type="image/jpeg")


@dataclass
class Downloads:
    fetched: list[str] = field(default_factory=list)

    async def __call__(self, media_id: str) -> Blob:
        self.fetched.append(media_id)
        if media_id == "missing":
            raise ProviderError("media expired", provider="whatsapp")
        return PHOTO


def photos(shop: Shop, chat: ScriptedChat, downloads: Downloads | None = None) -> Photos:
    return Photos(
        download=downloads or Downloads(),
        chat=chat,
        model="spark-test",
        catalog=shop.catalog,
    )


async def test_as_3_5_a_photo_is_described_and_matched_to_the_catalog(shop: Shop) -> None:
    chat = ScriptedChat(["  Mochila azul de tela con correas negras.  "])
    downloads = Downloads()
    warranty = shop.catalog.add_fact(
        shop.seller.seller_id, "policy", "Mochila azul de tela: garantía de seis meses."
    )

    reading = await photos(shop, chat, downloads).read(shop.seller.seller_id, "photo_1")

    assert reading.description == "Mochila azul de tela con correas negras."
    assert reading.matched_product_ids[0] == shop.ids["mochila-azul"]
    assert warranty not in reading.matched_product_ids  # products only
    assert shop.ids["borrador"] not in reading.matched_product_ids  # active only (R1)
    assert downloads.fetched == ["photo_1"]
    (sent,) = chat.requests
    assert (sent.model, sent.purpose, sent.tools, sent.temperature) == (
        "spark-test",
        "agent",
        (),
        0.0,
    )
    (system, user) = sent.messages
    assert system.role == "system"
    assert isinstance(user.content, tuple)
    assert [type(part) for part in user.content] == [TextPart, ImagePart]
    image = user.content[1]
    assert isinstance(image, ImagePart)
    assert image.image == PHOTO
    assert len(reading.calls) == 1


@pytest.mark.parametrize("answer", [NO_PRODUCT, "Sin producto.", "", "   "])
async def test_as_3_5_a_photo_without_a_product_has_no_description(shop: Shop, answer: str) -> None:
    reading = await photos(shop, ScriptedChat([answer])).read(shop.seller.seller_id, "photo_2")

    assert (reading.description, reading.matched_product_ids) == (None, ())


async def test_as_3_5_a_long_description_is_cut_to_a_search_query(shop: Shop) -> None:
    chat = ScriptedChat(["Mochila azul " * 40])

    reading = await photos(shop, chat).read(shop.seller.seller_id, "photo_3")

    assert reading.description is not None
    assert len(reading.description) <= 300


async def test_art_viii_a_photo_that_cannot_be_fetched_raises(shop: Shop) -> None:
    with pytest.raises(ProviderError):
        await photos(shop, ScriptedChat()).read(shop.seller.seller_id, "missing")
