"""Upload inspection for assets (T030, FR-010): what a file really is, and whether it fits.

The kind comes from the bytes, never from the client's content type: JPEG, PNG and WebP photos up
to 10 MB, and MP4 videos up to 60 MB and 15 s. Photos are decoded, which gives their size and
refuses anything Pillow cannot read; a video's duration comes from its ``mvhd`` box, so no ffmpeg
is needed to accept an upload.

A photo may have at most 40 megapixels. A few megabytes of PNG can hold far more, and the worker
decodes every pixel: a 150-megapixel photo needs more memory than the worker has.
"""

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, UnidentifiedImageError

MB = 1024 * 1024
IMAGE_MAX_BYTES = 10 * MB
IMAGE_MAX_PIXELS = 40_000_000
VIDEO_MAX_BYTES = 60 * MB
VIDEO_MAX_SECONDS = 15.0

Kind = Literal["image", "video"]


class MediaRejectedError(Exception):
    """The upload is not acceptable. ``message`` is Spanish, for the seller; ``too_large`` is 413."""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.too_large = too_large


@dataclass(frozen=True)
class MediaInfo:
    kind: Kind
    mime_type: str
    extension: str
    width: int | None
    height: int | None
    duration_s: float | None


def sniff(head: bytes) -> tuple[Kind, str, str] | None:
    """(kind, mime type, extension) from the first bytes, or None for anything else."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp", "webp"
    if head[4:8] == b"ftyp":
        return "video", "video/mp4", "mp4"
    return None


def inspect_media(data: bytes) -> MediaInfo:
    found = sniff(data[:16])
    if found is None:
        raise MediaRejectedError(
            "Formato no admitido: sube una foto JPEG, PNG o WebP, o un video MP4."
        )
    kind, mime_type, extension = found
    limit = IMAGE_MAX_BYTES if kind == "image" else VIDEO_MAX_BYTES
    if len(data) > limit:
        what = "La foto" if kind == "image" else "El video"
        raise MediaRejectedError(f"{what} pesa más de {limit // MB} MB.", too_large=True)
    if kind == "image":
        width, height = _image_size(data)
        return MediaInfo(kind, mime_type, extension, width, height, None)
    duration = mp4_duration(data)
    if duration is None:
        raise MediaRejectedError("No pudimos leer la duración del video.")
    if duration > VIDEO_MAX_SECONDS:
        raise MediaRejectedError(
            f"El video dura {duration:.0f} s y el máximo es {VIDEO_MAX_SECONDS:.0f} s."
        )
    return MediaInfo(kind, mime_type, extension, None, None, round(duration, 2))


def mp4_duration(data: bytes) -> float | None:
    """Seconds from the ``moov``/``mvhd`` box of an ISO media file, or None when absent."""
    moov = _find_box(data, 0, len(data), b"moov")
    if moov is None:
        return None
    mvhd = _find_box(data, moov[0], moov[1], b"mvhd")
    if mvhd is None or mvhd[1] - mvhd[0] < 20:
        return None
    start = mvhd[0]
    if data[start] == 1:  # version 1: 64-bit times
        if mvhd[1] - start < 32:
            return None
        timescale = int.from_bytes(data[start + 20 : start + 24], "big")
        duration = int.from_bytes(data[start + 24 : start + 32], "big")
    else:
        timescale = int.from_bytes(data[start + 12 : start + 16], "big")
        duration = int.from_bytes(data[start + 16 : start + 20], "big")
    return duration / timescale if timescale else None


def _find_box(data: bytes, start: int, end: int, kind: bytes) -> tuple[int, int] | None:
    """(payload start, payload end) of the first box of ``kind`` between ``start`` and ``end``."""
    offset = start
    while offset + 8 <= end:
        size = int.from_bytes(data[offset : offset + 4], "big")
        header = 8
        if size == 1:  # 64-bit size after the type
            if offset + 16 > end:
                return None
            size, header = int.from_bytes(data[offset + 8 : offset + 16], "big"), 16
        elif size == 0:  # the box runs to the end
            size = end - offset
        if size < header or offset + size > end:
            return None
        if data[offset + 4 : offset + 8] == kind:
            return offset + header, offset + size
        offset += size
    return None


TOO_MANY_PIXELS = (
    f"La foto tiene demasiados píxeles: el máximo es {IMAGE_MAX_PIXELS // 1_000_000} megapíxeles."
)


def _image_size(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
    except Image.DecompressionBombError as error:
        raise MediaRejectedError(TOO_MANY_PIXELS) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise MediaRejectedError("No pudimos leer la foto; puede estar dañada.") from error
    if width * height > IMAGE_MAX_PIXELS:
        raise MediaRejectedError(TOO_MANY_PIXELS)
    return width, height
