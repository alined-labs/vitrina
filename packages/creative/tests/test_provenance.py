"""Provenance metadata and the visible AI label on generated images (Art. IV.3, FR-015)."""

import json
from datetime import UTC, datetime
from io import BytesIO

import numpy as np
from PIL import Image

from vitrina_core.providers import Blob
from vitrina_creative.api import (
    AI_LABEL,
    Provenance,
    ai_label,
    embed_provenance,
    provenance_json,
    provenance_record,
    read_provenance,
)
from vitrina_creative.imaging import decode_rgb
from vitrina_creative.testing import good_scenes, source_photo

PROVENANCE = Provenance(
    generator="image-model-from-settings",
    source_sha256=source_photo().image.sha256,
    verification="passed",
    created_at=datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
)


def test_generated_images_declare_iptc_ai_provenance_without_changing_pixels() -> None:
    scene = good_scenes()[0].image
    stamped = embed_provenance(scene, PROVENANCE)
    fields = read_provenance(stamped)
    assert fields["ai_generated"] == "true"
    assert fields["digital_source_type"].endswith("/digitalsourcetype/trainedAlgorithmicMedia")
    assert fields["generator"] == "image-model-from-settings"
    assert fields["source_photo_sha256"] == source_photo().image.sha256
    assert fields["verification"] == "passed"
    assert "<Iptc4xmpExt:DigitalSourceType>" in fields["XML:com.adobe.xmp"]
    assert AI_LABEL in fields["XML:com.adobe.xmp"]
    assert (decode_rgb(stamped) == decode_rgb(scene)).all()


def test_a_composite_scene_says_so() -> None:
    composite = Provenance(
        **{**PROVENANCE.__dict__, "source_type": "compositeWithTrainedAlgorithmicMedia"}
    )
    fields = read_provenance(embed_provenance(good_scenes()[0].image, composite))
    assert fields["digital_source_type"].endswith("compositeWithTrainedAlgorithmicMedia")


def test_fr_015_the_export_record_names_model_source_and_verification() -> None:
    record = provenance_record(PROVENANCE, creative_id="c-1", image_format="story_9x16")
    document = json.loads(provenance_json([record]))
    (entry,) = document["creatives"]
    assert entry == {
        "creative_id": "c-1",
        "format": "story_9x16",
        "ai_generated": "true",
        "digital_source_type": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
        "generator": "image-model-from-settings",
        "source_photo_sha256": source_photo().image.sha256,
        "verification": "passed",
        "created_at": "2026-10-27T15:00:00+00:00",
        "label": "Imagen generada con IA",
    }


def test_the_visible_label_sits_bottom_left_and_leaves_the_rest_untouched() -> None:
    scene = good_scenes()[0].image
    before, after = decode_rgb(scene), decode_rgb(ai_label(scene))
    assert after.shape == before.shape
    height, width = before.shape[:2]
    changed = np.any(after != before, axis=2)
    rows, cols = np.nonzero(changed)
    assert rows.min() > height * 0.85  # only the bottom band
    assert cols.max() < width * 0.6  # only the left side
    assert changed.mean() < 0.05


def test_the_label_scales_with_a_story_format_image() -> None:
    story = Image.new("RGB", (1080, 1920), (240, 236, 228))
    buffer = BytesIO()
    story.save(buffer, format="PNG")
    labelled = decode_rgb(ai_label(Blob(data=buffer.getvalue(), mime_type="image/png")))
    rows, cols = np.nonzero(np.any(labelled != np.asarray(story), axis=2))
    assert labelled.shape == (1920, 1080, 3)
    assert 30 <= rows.max() - rows.min() <= 120  # readable, not dominant
    assert rows.min() > 1920 * 0.9
    assert cols.max() < 1080 * 0.6  # bottom-left corner
