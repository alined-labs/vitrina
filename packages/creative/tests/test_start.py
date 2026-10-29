"""I-13: ``start_job``'s snapshot applies the route's rules for any caller (T034, T071)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import pytest
from pydantic import ValidationError

from vitrina_catalog.api import InMemoryCatalog
from vitrina_catalog.sellers import InMemorySellerStore, SellerCreate
from vitrina_core.http import ProblemCode, ProblemError
from vitrina_core.ids import new_id
from vitrina_core.storage import LocalBlobStore, blob_key
from vitrina_creative.assets import AssetRecord, InMemoryAssetStore
from vitrina_creative.schemas import CreativeJobCreate
from vitrina_creative.start import job_payload
from vitrina_creative.tasks import load_input

SELLER = UUID("00000000-0000-7000-8000-00000000003a")
OTHER = UUID("00000000-0000-7000-8000-00000000003b")
BRIEF = UUID("00000000-0000-7000-8000-00000000003c")


@dataclass
class Shop:
    catalog: InMemoryCatalog
    sellers: InMemorySellerStore
    assets: InMemoryAssetStore
    product: UUID
    bare: UUID
    photos: tuple[AssetRecord, AssetRecord]

    async def payload(self, params: CreativeJobCreate, seller_id: UUID = SELLER) -> dict[str, Any]:
        return await job_payload(
            seller_id, params, catalog=self.catalog, sellers=self.sellers, assets=self.assets
        )


def record(product_id: UUID, name: str, kind: Literal["image", "video"] = "image") -> AssetRecord:
    ext = "png" if kind == "image" else "mp4"
    return AssetRecord(
        id=new_id(),
        seller_id=SELLER,
        product_id=product_id,
        kind=kind,
        storage_key=blob_key(SELLER, "assets", name, ext),
        sha256=name,
    )


@pytest.fixture
async def shop() -> Shop:
    catalog = InMemoryCatalog()
    sellers = InMemorySellerStore()
    seller = SellerCreate(
        business_name="Tejidos Vecina",
        owner_name="Ana",
        whatsapp_number="+573000000601",
        city="Medellín",
    )
    await sellers.create(seller, auth_subject=None, seller_id=SELLER)
    product = catalog.add_product(
        SELLER, "Bolso tejido a mano", price_cop=85000, stock=3, description="Hilo de algodón."
    )
    bare = catalog.add_product(SELLER, "Bolso sin fotos", price_cop=60000)
    catalog.add_fact(SELLER, "shipping", "Envío a Medellín $12.000, 2–3 días")
    catalog.add_fact(SELLER, "faq", "Borrador que aún no se publica", status="draft")
    assets = InMemoryAssetStore()
    front, side = record(product, "front"), record(product, "side")
    for item in (front, side, record(product, "clip", "video")):
        await assets.add(item)
    return Shop(catalog, sellers, assets, product, bare, (front, side))


async def test_the_snapshot_holds_the_product_its_active_facts_the_voice_and_the_photos(
    shop: Shop,
) -> None:
    payload = await shop.payload(CreativeJobCreate(product_id=shop.product))

    assert payload["product"]["name"] == "Bolso tejido a mano"
    assert payload["product"]["price_cop"] == 85000
    assert payload["facts"] == ["Envío a Medellín $12.000, 2–3 días"]  # the draft stays out
    assert payload["seller"]["business_name"] == "Tejidos Vecina"
    assert {photo["key"] for photo in payload["photos"]} == {
        photo.storage_key for photo in shop.photos
    }  # images only: the clip is not a photo


async def test_a_brief_travels_in_the_params(shop: Shop) -> None:
    params = CreativeJobCreate(product_id=shop.product, brief_id=BRIEF, scene_brief="Feria")

    payload = await shop.payload(params)

    assert payload["params"]["brief_id"] == str(BRIEF)
    assert payload["params"]["scene_brief"] == "Feria"


async def test_a_jobs_hooks_reach_the_pipeline_input(shop: Shop, tmp_path: Path) -> None:
    params = CreativeJobCreate(product_id=shop.product, brief_id=BRIEF, hooks=["question"])
    payload = await shop.payload(params)
    storage = LocalBlobStore(tmp_path, public_url="http://storage.test")
    for photo in payload["photos"]:
        await storage.put(photo["key"], b"png", "image/png")

    data = await load_input(payload, SELLER, storage)

    assert payload["params"]["hooks"] == ["question"]
    assert data.hooks == ("question",)


def test_an_unknown_hook_is_refused(shop: Shop) -> None:
    with pytest.raises(ValidationError):
        CreativeJobCreate.model_validate({"product_id": str(shop.product), "hooks": ["meme"]})


async def test_asset_ids_choose_among_the_products_photos(shop: Shop) -> None:
    front = shop.photos[0]
    payload = await shop.payload(CreativeJobCreate(product_id=shop.product, asset_ids=[front.id]))

    assert [photo["asset_id"] for photo in payload["photos"]] == [str(front.id)]


async def test_another_sellers_product_is_not_found(shop: Shop) -> None:
    with pytest.raises(ProblemError) as caught:
        await shop.payload(CreativeJobCreate(product_id=shop.product), seller_id=OTHER)

    assert (caught.value.code, caught.value.status) == (ProblemCode.NOT_FOUND, 404)


async def test_a_product_without_photos_or_with_foreign_ones_is_refused_on_asset_ids(
    shop: Shop,
) -> None:
    for params in (
        CreativeJobCreate(product_id=shop.bare),
        CreativeJobCreate(product_id=shop.bare, asset_ids=[shop.photos[0].id]),
    ):
        with pytest.raises(ProblemError) as caught:
            await shop.payload(params)
        assert caught.value.status == 400
        assert [error.field for error in caught.value.errors] == ["asset_ids"]


async def test_a_seller_who_never_signed_up_is_not_found(shop: Shop) -> None:
    shop.sellers.rows.clear()

    with pytest.raises(ProblemError) as caught:
        await shop.payload(CreativeJobCreate(product_id=shop.product))

    assert caught.value.code == ProblemCode.NOT_FOUND
