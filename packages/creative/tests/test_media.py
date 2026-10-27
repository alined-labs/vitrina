"""T030 upload inspection: kinds from bytes, FR-010 limits, MP4 duration without ffmpeg."""

from io import BytesIO

import pytest
from PIL import Image

from vitrina_creative.media import (
    IMAGE_MAX_BYTES,
    MediaRejectedError,
    inspect_media,
    mp4_duration,
)


def box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def mp4(seconds: float, *, version: int = 0, timescale: int = 1000) -> bytes:
    duration = round(seconds * timescale)
    if version == 0:
        times = (
            (0).to_bytes(4, "big") * 2 + timescale.to_bytes(4, "big") + duration.to_bytes(4, "big")
        )
    else:
        times = (
            (0).to_bytes(8, "big") * 2 + timescale.to_bytes(4, "big") + duration.to_bytes(8, "big")
        )
    mvhd = bytes([version, 0, 0, 0]) + times + bytes(80)
    return (
        box(b"ftyp", b"isom" + bytes(4) + b"isomiso2")
        + box(b"moov", box(b"mvhd", mvhd))
        + box(b"mdat", bytes(64))
    )


def photo(fmt: str, size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (178, 34, 52)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("fmt", "mime", "ext"),
    [("PNG", "image/png", "png"), ("JPEG", "image/jpeg", "jpg"), ("WEBP", "image/webp", "webp")],
)
def test_fr_010_photos_are_recognised_from_their_bytes(fmt: str, mime: str, ext: str) -> None:
    info = inspect_media(photo(fmt))
    assert (info.kind, info.mime_type, info.extension, info.width, info.height) == (
        "image",
        mime,
        ext,
        64,
        48,
    )
    assert info.duration_s is None


def test_fr_010_a_short_mp4_is_a_video_with_its_duration() -> None:
    info = inspect_media(mp4(10))
    assert (info.kind, info.mime_type, info.extension, info.duration_s) == (
        "video",
        "video/mp4",
        "mp4",
        10.0,
    )


def test_mp4_duration_reads_version_1_headers_and_64_bit_boxes() -> None:
    assert mp4_duration(mp4(7.5, version=1, timescale=90_000)) == 7.5
    # A box with a 64-bit size (size field 1) before moov must be skipped correctly.
    wide_free_box = (1).to_bytes(4, "big") + b"free" + (16 + 4).to_bytes(8, "big") + bytes(4)
    assert mp4_duration(wide_free_box + mp4(3)) == 3.0
    assert mp4_duration(box(b"ftyp", b"isom") + box(b"mdat", bytes(8))) is None


def test_fr_010_a_video_over_15_seconds_is_refused() -> None:
    with pytest.raises(MediaRejectedError, match="máximo es 15 s"):
        inspect_media(mp4(16))


def test_fr_010_a_photo_over_10_mb_is_too_large() -> None:
    data = b"\x89PNG\r\n\x1a\n" + bytes(IMAGE_MAX_BYTES)
    with pytest.raises(MediaRejectedError) as caught:
        inspect_media(data)
    assert caught.value.too_large


@pytest.mark.parametrize("data", [b"hola, esto no es una foto", b"\x89PNG\r\n\x1a\n" + bytes(64)])
def test_anything_else_or_a_broken_photo_is_refused(data: bytes) -> None:
    with pytest.raises(MediaRejectedError) as caught:
        inspect_media(data)
    assert not caught.value.too_large
