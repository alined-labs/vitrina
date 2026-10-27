"""T083 showcase helpers: slugs, the hourly address hash, and the published image (FR-072)."""

import re
from datetime import UTC, datetime, timedelta

from vitrina_core.providers import Blob
from vitrina_creative.provenance import Provenance, embed_provenance, read_provenance
from vitrina_creative.showcase import address_hash, new_slug, showcase_image, window_start
from vitrina_creative.testing import good_scenes

SLUG = re.compile(r"^[a-z0-9-]{6,40}$")
KEY = b"k" * 32


def test_slugs_fit_the_contract_and_differ_for_the_same_name() -> None:
    names = ["Bolso tejido a mano", "Ñame del Pacífico 🍠", "!!!", "x" * 200, "Café"]
    slugs = [new_slug(name) for name in names]

    assert all(SLUG.fullmatch(slug) for slug in slugs), slugs
    assert slugs[0].startswith("bolso-tejido-a-mano-")
    assert slugs[2].startswith("vitrina-")
    assert new_slug("Café") != new_slug("Café")


def test_the_address_hash_hides_the_address_and_changes_every_hour() -> None:
    now = datetime(2026, 10, 27, 15, 42, 7, tzinfo=UTC)
    window = window_start(now)

    assert window == datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    first = address_hash("203.0.113.9", KEY, window)
    assert first == address_hash("203.0.113.9", KEY, window)
    assert first != address_hash("203.0.113.9", KEY, window + timedelta(hours=1))
    assert first != address_hash("203.0.113.10", KEY, window)
    assert "203.0.113.9" not in first


def test_a_published_scene_carries_both_marks_and_keeps_its_provenance() -> None:
    provenance = Provenance(
        generator="muse-test",
        source_sha256="cd" * 32,
        verification="passed",
        created_at=datetime.now(UTC),
    )
    scene = embed_provenance(good_scenes()[0].image, provenance)

    published = showcase_image(scene)

    assert published.data != scene.data
    assert read_provenance(published)["generator"] == "muse-test"
    assert Blob(data=published.data, mime_type="image/png").sha256 != scene.sha256
