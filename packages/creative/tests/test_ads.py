"""T042 without a database: the export package, the link in the copy, the hint ranking."""

import io
import json
import zipfile
from datetime import UTC, datetime
from uuid import UUID

from vitrina_core.providers import Blob
from vitrina_creative.ads import Ad, export_package, fill_link, rank_by_hint
from vitrina_creative.provenance import (
    Provenance,
    embed_provenance,
    provenance_of,
    publishable,
    read_provenance,
)
from vitrina_creative.testing import good_scenes

LINK = "https://wa.me/573000000701?text=Hola%2C%20vi%20el%20anuncio%20%23A7"
WHEN = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def scene() -> Blob:
    provenance = Provenance(
        generator="muse-test", source_sha256="ab" * 32, verification="passed", created_at=WHEN
    )
    return embed_provenance(good_scenes()[0].image, provenance)


def approved_ad(code: str | None = "A7", link: str | None = LINK) -> Ad:
    return Ad(
        id=UUID("00000000-0000-7000-8000-00000000004a"),
        seller_id=UUID("00000000-0000-7000-8000-00000000004b"),
        product_id=UUID("00000000-0000-7000-8000-00000000004c"),
        creative_id=UUID("00000000-0000-7000-8000-00000000004d"),
        copy_variant_id=UUID("00000000-0000-7000-8000-00000000004e"),
        code=code,
        wa_link=link,
        status="approved",
        created_at=WHEN,
        approved_at=WHEN,
    )


DETAILS = {
    "format": "catalog_1x1",
    "verification": {"status": "passed", "mask_iou": 0.97, "color_delta": 0.08},
    "copy_text": "Bolso tejido a mano por $85.000, veci. {wa_link}",
}


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_fill_link_puts_the_ads_link_where_the_copy_asks_for_it() -> None:
    assert fill_link("Escríbenos {wa_link}", LINK) == f"Escríbenos {LINK}"
    assert fill_link("Escríbenos {wa_link}", None) == "Escríbenos"


def test_as_2_5_the_export_holds_the_labelled_image_the_copy_and_provenance() -> None:
    files = unzip(export_package(approved_ad(), DETAILS, scene(), label=True))

    assert set(files) == {"A7-catalog_1x1.png", "copy.txt", "provenance.json"}
    assert files["copy.txt"].decode() == f"Bolso tejido a mano por $85.000, veci. {LINK}\n"
    report = json.loads(files["provenance.json"])
    assert report["ad"]["code"] == "A7"
    assert report["creatives"][0]["generator"] == "muse-test"
    assert report["creatives"][0]["label_applied"] == "true"
    assert report["verification"]["status"] == "passed"
    image = Blob(data=files["A7-catalog_1x1.png"], mime_type="image/png")
    assert image.data != scene().data  # the label is drawn
    assert read_provenance(image)["generator"] == "muse-test"  # and provenance survives it


def test_without_the_label_the_export_ships_the_scene_as_generated() -> None:
    files = unzip(export_package(approved_ad(), DETAILS, scene(), label=False))

    assert files["A7-catalog_1x1.png"] == scene().data
    assert json.loads(files["provenance.json"])["creatives"][0]["label_applied"] == "false"


def test_provenance_reads_back_and_an_unmarked_image_has_none() -> None:
    found = provenance_of(scene())

    assert found is not None
    assert (found.generator, found.verification, found.created_at) == ("muse-test", "passed", WHEN)
    assert provenance_of(good_scenes()[0].image) is None
    assert publishable(good_scenes()[0].image, label=False) is good_scenes()[0].image


def test_the_hint_puts_matching_ads_first_and_keeps_the_newest_among_equals() -> None:
    rows = [
        {"id": 1, "copy_text": "Bolso azul, listo para regalar", "scene_prompt": None},
        {"id": 2, "copy_text": "Bolso rojo de algodón", "scene_prompt": "mesa de café"},
        {"id": 3, "copy_text": "Bolso rojo, hecho a mano", "scene_prompt": None},
    ]

    assert [row["id"] for row in rank_by_hint(rows, {"color": "Rojo"})] == [2, 3, 1]
    assert [row["id"] for row in rank_by_hint(rows, {"lugar": "cafe"})] == [2, 1, 3]
    assert [row["id"] for row in rank_by_hint(rows, None)] == [1, 2, 3]
