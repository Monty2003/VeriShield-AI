"""
Central image decoding.

Every stage that touches pixels goes through here, so format support is added
once rather than in four places that drift apart.

The reason this module exists: modern phones shoot HEIC by default (Samsung,
Apple, and most recent Android), and neither OpenCV nor Pillow reads it out of
the box. A verification service that rejects the native format of the camera
most of its users hold is broken for the majority of real submissions, however
well it performs on the JPEGs it was tested with.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

# Register HEIC/HEIF with Pillow at import time. Wrapped because the decoder is
# an optional dependency: without it the service still handles JPEG and PNG,
# and says plainly that it cannot read HEIC rather than failing obscurely.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the deployment
    HEIF_AVAILABLE = False


HEIF_MAGIC = (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypmsf1")


def looks_like_heif(data: bytes) -> bool:
    """
    Detect HEIF/HEIC from the file's own bytes.

    Sniffing rather than trusting the filename: uploads arrive with wrong or
    missing extensions all the time, and a document rejected because someone
    renamed it is a support ticket, not a security decision.
    """
    head = data[:32]
    return any(magic in head for magic in HEIF_MAGIC)


class UnsupportedImageError(ValueError):
    """Raised when an image cannot be decoded, with a reason a human can act on."""


def open_pil(data: bytes) -> Image.Image:
    """Decode to a Pillow image, HEIC included."""
    try:
        return Image.open(io.BytesIO(data))
    except Exception as exc:
        if looks_like_heif(data) and not HEIF_AVAILABLE:
            raise UnsupportedImageError(
                "This looks like a HEIC/HEIF photo, which most current phones "
                "produce by default, but no HEIF decoder is installed. Install "
                "it with: pip install pillow-heif"
            ) from exc
        raise UnsupportedImageError(f"could not decode image: {exc}") from exc


def decode_image(data: bytes) -> np.ndarray:
    """
    Decode to a BGR array for OpenCV.

    Tries OpenCV first because it is faster and handles the common cases, then
    falls back to Pillow, which is what actually reads HEIC.
    """
    array = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if array is not None:
        return array

    pil = open_pil(data)
    # EXIF orientation is applied here rather than left to each caller. A
    # portrait photo stored rotated with an orientation tag will otherwise be
    # analysed sideways, which quietly breaks text detection and every
    # region coordinate reported back to the reviewer.
    try:
        from PIL import ImageOps

        pil = ImageOps.exif_transpose(pil)
    except Exception:  # pragma: no cover - orientation is best-effort
        pass

    rgb = pil.convert("RGB")
    return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)


def source_format(data: bytes) -> str:
    """
    Report the original container format, uppercase, or "" if unknown.

    Forensics needs this: Error Level Analysis compares JPEG compression
    histories and is meaningless on a format that has none, so the stage must
    be able to tell what it was actually given.
    """
    if looks_like_heif(data):
        return "HEIF"
    try:
        return (open_pil(data).format or "").upper()
    except UnsupportedImageError:
        return ""


def to_jpeg(data: bytes, quality: int = 95) -> bytes:
    """
    Re-encode any supported image as JPEG.

    Used to give HEIC captures a JPEG baseline before tamper generation. Note
    what this does and does not achieve: it makes ELA *applicable*, because the
    result has a JPEG history -- but that history is uniform across the whole
    frame, so it carries no information on its own. The signal only appears
    once a region is edited and re-encoded separately.
    """
    pil = open_pil(data)
    try:
        from PIL import ImageOps

        pil = ImageOps.exif_transpose(pil)
    except Exception:  # pragma: no cover
        pass

    buffer = io.BytesIO()
    pil.convert("RGB").save(buffer, "JPEG", quality=quality, subsampling=0)
    return buffer.getvalue()
