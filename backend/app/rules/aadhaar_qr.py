"""
Aadhaar Secure QR parsing (Layer 8, offline).

This is the strongest verification in the project, and the only one that
reaches outside the document.

Every other check asks whether a document is internally consistent. A checksum
proves a number is well-formed; it cannot prove UIDAI ever issued it, or that
the name printed beside it is the name UIDAI holds. The Secure QR can, because
UIDAI signed its contents with a private key nobody else has.

What that buys, concretely
--------------------------
The QR carries the holder's name, date of birth, gender, address and
photograph as UIDAI recorded them. It cannot be edited: altering one byte
invalidates the signature, and regenerating it would require UIDAI's key.

So the printed side of the card becomes checkable against an authority:

  * **Name, date of birth, gender, address changed on the card** -- previously
    undetectable, because none of those has a checksum. Now they simply
    disagree with the QR.
  * **Photograph substituted** -- the QR contains UIDAI's own photograph of the
    holder. Three separate attempts at detecting this from image statistics
    measured at chance; comparing against the issuer's copy does not need
    statistics at all.
  * **Aadhaar number** -- the reference ID begins with the last four digits, so
    the printed number can be checked against the QR as well as against its
    own Verhoeff digit.

A forger's remaining options are to remove the QR, which is visible, or leave
the original in place, which then contradicts whatever they edited.

Two formats exist in the wild
-----------------------------
Older cards carry an unversioned QR whose first field is the email/mobile
flag. Newer ones begin with a version marker ("V2".."V5") and add the
photograph. Both appear among real cards -- both were found in the sample this
parser was written against -- so both are handled rather than assumed.

Privacy
-------
Nothing here returns or logs a full Aadhaar number, and reasons quote only the
last four digits. The parsed fields exist to be COMPARED against what the card
shows; they are not something the caller should store.
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

# UIDAI signs the QR payload with RSA-2048, so the signature is always the
# final 256 bytes of the decompressed data.
SIGNATURE_BYTES = 256

# Field delimiter inside the payload.
DELIMITER = b"\xff"

# JPEG2000 codestream marker. The photograph, when present, starts here.
JP2_MAGIC = b"\xff\x4f\xff\x51"

# Field order after the optional version marker.
_FIELDS = (
    "email_mobile_flag",
    "reference_id",
    "name",
    "date_of_birth",
    "gender",
    "care_of",
    "district",
    "landmark",
    "house",
    "location",
    "pincode",
    "post_office",
    "state",
    "street",
    "sub_district",
    "vtc",
)


@dataclass
class AadhaarQR:
    """Parsed contents of an Aadhaar Secure QR."""

    version: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    photo_jp2: bytes | None = None
    signature: bytes | None = None
    signed_payload: bytes | None = None
    raw_length: int = 0
    parse_errors: list[str] = field(default_factory=list)

    @property
    def last_four_digits(self) -> str:
        """
        Last four digits of the Aadhaar number.

        The reference ID is those four digits followed by an issue timestamp,
        so this is available without the QR ever carrying the full number.
        """
        reference = self.fields.get("reference_id", "")
        return reference[:4] if len(reference) >= 4 else ""

    @property
    def has_photo(self) -> bool:
        return bool(self.photo_jp2)

    @property
    def date_of_birth_parsed(self) -> date | None:
        """
        Parse the date of birth, which UIDAI writes in several shapes.

        Older cards carry only a year of birth; newer ones a full date in
        either DD-MM-YYYY or YYYY-MM-DD.
        """
        value = self.fields.get("date_of_birth", "").strip()
        if not value:
            return None
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y"):
            try:
                parsed = datetime.strptime(value, fmt).date()
                # A bare year is a year, not the first of January.
                return None if fmt == "%Y" else parsed
            except ValueError:
                continue
        return None

    @property
    def year_of_birth(self) -> int | None:
        value = self.fields.get("date_of_birth", "").strip()
        for token in value.replace("/", "-").split("-"):
            if len(token) == 4 and token.isdigit():
                return int(token)
        return None

    def masked_summary(self) -> dict[str, object]:
        """A description safe to log: presence and shape, never the values."""
        return {
            "version": self.version or "legacy",
            "aadhaar_last_four": self.last_four_digits,
            "has_photo": self.has_photo,
            "has_signature": self.signature is not None,
            "fields_present": sorted(k for k, v in self.fields.items() if v),
        }


def decode_qr_payload(payload: bytes | str) -> bytes:
    """
    Turn the QR's numeric payload into the decompressed byte stream.

    The QR encodes a very large integer as decimal digits; that integer's
    big-endian bytes are gzip-compressed.
    """
    text = payload.decode("ascii", "ignore") if isinstance(payload, bytes) else payload
    text = text.strip()

    if not text.isdigit():
        raise ValueError("QR payload is not the numeric form an Aadhaar Secure QR uses")

    number = int(text)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")

    if raw[:2] != b"\x1f\x8b":
        raise ValueError("decoded payload is not gzip data")

    return gzip.decompress(raw)


def parse_secure_qr(payload: bytes | str) -> AadhaarQR:
    """Parse an Aadhaar Secure QR payload into fields, photo and signature."""
    data = decode_qr_payload(payload)
    result = AadhaarQR(raw_length=len(data))

    # The version marker, when present, is a short ASCII token beginning "V".
    # Its absence means the older layout, where the first field is the
    # email/mobile flag instead.
    first, _, _ = data.partition(DELIMITER)
    versioned = first[:1] == b"V" and len(first) <= 3

    result.version = first.decode("ascii", "ignore") if versioned else ""
    field_count = len(_FIELDS) + (1 if versioned else 0)

    parts = data.split(DELIMITER, field_count)
    if len(parts) < field_count:
        result.parse_errors.append(
            f"Expected {field_count} delimited fields, found {len(parts)}."
        )
        return result

    offset = 1 if versioned else 0
    for index, name in enumerate(_FIELDS):
        raw_value = parts[index + offset]
        result.fields[name] = raw_value.decode("utf-8", "replace").strip()

    tail = parts[-1]

    # The signature is always the last 256 bytes; whatever precedes it, after
    # the final text field, is the photograph.
    if len(tail) >= SIGNATURE_BYTES:
        result.signature = tail[-SIGNATURE_BYTES:]
        remainder = tail[:-SIGNATURE_BYTES]

        # Everything except the signature is what UIDAI signed.
        result.signed_payload = data[: len(data) - SIGNATURE_BYTES]

        photo_start = remainder.find(JP2_MAGIC)
        if photo_start >= 0:
            result.photo_jp2 = remainder[photo_start:]
    else:
        result.parse_errors.append(
            f"Payload tail is only {len(tail)} bytes; a signature needs "
            f"{SIGNATURE_BYTES}."
        )

    return result


def decode_qr_photo(qr: AadhaarQR) -> np.ndarray | None:
    """
    Decode the embedded photograph to a BGR array.

    UIDAI stores it as a JPEG2000 codestream at roughly 60x60 -- enough to
    compare against the printed portrait, but small, which is why callers
    should treat a face-similarity score from it as weaker than one between
    two full-size images.
    """
    if not qr.photo_jp2:
        return None
    try:
        import cv2

        return cv2.imdecode(np.frombuffer(qr.photo_jp2, np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001 -- an undecodable photo is not a crash
        return None


# Results by (digest of the image, thorough). A case reads each Aadhaar image's
# QR once in its own assessment and again in the front/back cross-check; the
# second read was repeating the most expensive stage in the pipeline.
_PAYLOAD_CACHE: dict[tuple[bytes, bool], tuple[bytes, ...]] = {}
_PAYLOAD_CACHE_SIZE = 8

# Longest side of the reduced copy the QR locator works on. Measured on the
# genuine set: at 1600px the dense Secure QR is found in 1 of 4 images, at
# 3200px in 3 of 4, at ~0.1s on images with no QR at all.
_LOCATE_SIDE = 3200


# The primary decoder. zxing-cpp reads the dense Secure QR where zbar does not,
# and does it in about a tenth of a second instead of three to five: measured
# on the genuine set, it read every QR zbar read, byte for byte, and one more.
# It also ships self-contained wheels, where zbar needs a system library that
# a fresh Linux server does not have.
try:
    import zxingcpp
except ImportError:  # pragma: no cover - depends on the installation
    zxingcpp = None

# Whole-photograph scales for zxing. Downscaling helps as often as not: one
# genuine back decodes at 0.75 and 0.5 and at no other size.
_ZXING_SCALES = (1.0, 0.75, 0.5)

# Rescue of a QR that was LOCATED but failed its error correction -- which is
# what blur and small modules look like. Straightening it to a square this many
# pixels across and sharpening it recovered both such QRs in the genuine set.
_RESCUE_SIDES = (780, 1040)

# An image this small is a screenshot, a download or a heavily compressed
# forward, not a camera photograph -- and in it a dense QR sits at one or two
# pixels per module, below what any decoder resolves. Upscaling is the only
# thing that helps, and at this size it is cheap. Measured: a genuine back
# reduced to 1600px decodes only after a 1.5x upscale, and one reduced to
# 1080px only through the whole-image ladder that card fronts used to skip.
_SMALL_IMAGE_SIDE = 2000
_SMALL_IMAGE_UPSCALES = (1.5, 2.0)
_UNSHARP_AMOUNTS = (0.8, 1.5)
_RESCUE_MAX_QUADS = 3


def _zxing_read(gray: np.ndarray) -> tuple[list[bytes], list[np.ndarray]]:
    """Decoded payloads, and the corners of each QR found but not decoded."""
    decoded: list[bytes] = []
    located: list[np.ndarray] = []
    for result in zxingcpp.read_barcodes(
        gray,
        formats=zxingcpp.BarcodeFormat.QRCode,
        try_rotate=True,
        try_downscale=True,
        return_errors=True,
    ):
        if result.valid:
            data = bytes(result.bytes)
            if data and data not in decoded:
                decoded.append(data)
        else:
            corner = result.position
            located.append(
                np.array(
                    [
                        [corner.top_left.x, corner.top_left.y],
                        [corner.top_right.x, corner.top_right.y],
                        [corner.bottom_right.x, corner.bottom_right.y],
                        [corner.bottom_left.x, corner.bottom_left.y],
                    ],
                    dtype=np.float32,
                )
            )
    return decoded, located


def _rescue(gray: np.ndarray, quads: list[np.ndarray]) -> list[bytes]:
    """Straighten each located QR, sharpen it, and decode again."""
    import cv2

    for quad in quads[:_RESCUE_MAX_QUADS]:
        for side in _RESCUE_SIDES:
            margin = side // 25  # the quiet zone the decoder expects
            target = np.array(
                [
                    [margin, margin],
                    [margin + side, margin],
                    [margin + side, margin + side],
                    [margin, margin + side],
                ],
                dtype=np.float32,
            )
            warped = cv2.warpPerspective(
                gray,
                cv2.getPerspectiveTransform(quad, target),
                (side + 2 * margin, side + 2 * margin),
                flags=cv2.INTER_CUBIC,
                borderValue=255,
            )
            blurred = cv2.GaussianBlur(warped, (0, 0), 3)
            for amount in _UNSHARP_AMOUNTS:
                sharpened = cv2.addWeighted(warped, 1 + amount, blurred, -amount, 0)
                decoded, _ = _zxing_read(sharpened)
                if decoded:
                    return decoded
    return []


def _is_small(image: np.ndarray) -> bool:
    return max(image.shape[:2]) <= _SMALL_IMAGE_SIDE


def _zxing_payloads(image: np.ndarray) -> list[bytes]:
    """zxing over the whole photograph at a few scales, then the rescue."""
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    located: list[np.ndarray] = []
    for scale in _ZXING_SCALES:
        candidate = (
            gray
            if scale == 1.0
            else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        )
        decoded, failed = _zxing_read(candidate)
        if decoded:
            return decoded
        located.extend(quad / scale for quad in failed)
    rescued = _rescue(gray, located)
    if rescued or not _is_small(image):
        return rescued
    for scale in _SMALL_IMAGE_UPSCALES:
        decoded, _ = _zxing_read(
            cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        )
        if decoded:
            return decoded
    return []


def available_decoders() -> list[str]:
    """QR decoders this installation can use, primary first. Empty means no QR is ever read."""
    found = ["zxing-cpp"] if zxingcpp is not None else []
    try:
        import pyzbar.pyzbar  # noqa: F401

        found.append("zbar")
    except ImportError:
        pass
    return found


def _zbar_payloads(image: np.ndarray) -> list[bytes]:
    import cv2
    from pyzbar.pyzbar import decode as zbar_decode

    found: list[bytes] = []
    for result in zbar_decode(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)):
        if result.data and result.data not in found:
            found.append(result.data)
    return found


def _locate_qr_regions(image: np.ndarray) -> list[np.ndarray]:
    """Crops around each QR the locator finds, in full-resolution pixels."""
    import cv2

    if not hasattr(cv2, "QRCodeDetectorAruco"):
        return []
    height, width = image.shape[:2]
    factor = min(1.0, _LOCATE_SIDE / max(height, width))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if factor < 1.0:
        gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    try:
        found, points = cv2.QRCodeDetectorAruco().detectMulti(gray)
    except cv2.error:
        return []
    if not found or points is None:
        return []

    regions = []
    for quad in points:
        corners = np.asarray(quad, dtype=np.float32).reshape(-1, 2) / factor
        (x0, y0), (x1, y1) = corners.min(axis=0), corners.max(axis=0)
        mx, my = (x1 - x0) * 0.15, (y1 - y0) * 0.15
        region = image[
            max(0, int(y0 - my)) : min(height, int(y1 + my)),
            max(0, int(x0 - mx)) : min(width, int(x1 + mx)),
        ]
        if region.size:
            regions.append(region)
    return regions


def extract_qr_payloads(image_bytes: bytes, thorough: bool = True) -> list[bytes]:
    """
    Find and read every QR code on a document image, cheapest route first.

      0. zxing-cpp over the whole photograph at three scales, then -- for a
         QR it located but could not decode -- straightened and sharpened.
         About 0.1-0.4 s, and on the genuine set it reads 5 of 7 card backs
         where the zbar routes below read 2. Skipped if not installed.
      1. zbar, one pass at native resolution.
      2. Locate the QR on a reduced copy and upscale only that region. Cheap,
         because the region is a fraction of the photograph.
      3. If `thorough`: upscale the WHOLE photograph, 1.5x then 2x. This was
         the only route before, and it is kept exactly as it was -- resize
         the colour image, then convert -- because one genuine QR decodes
         that way and not when the order is swapped.

    Routes 1-3 are kept as fallbacks, and run only if pyzbar and its zbar
    library are present.

    Route 3 is what cost ~3.5 seconds per document, spent mostly on card
    fronts that carry no QR. Callers pass thorough=False where a QR is not
    expected, so a front with no QR is done after routes 1 and 2.

    The Aadhaar Secure QR is dense, and a phone photograph often renders its
    modules just below what a decoder resolves at native size; that is why
    upscaling exists at all.
    """
    import cv2

    try:
        import pyzbar.pyzbar  # noqa: F401

        have_zbar = True
    except ImportError:  # the package, or the zbar shared library it loads
        have_zbar = False
    if zxingcpp is None and not have_zbar:
        return []

    digest = hashlib.blake2b(image_bytes, digest_size=16).digest()
    # A thorough result answers a non-thorough question too.
    for key in ((digest, True), (digest, thorough)):
        if key in _PAYLOAD_CACHE:
            return list(_PAYLOAD_CACHE[key])

    from app.core.imaging import decode_image

    image = decode_image(image_bytes)
    payloads = _zxing_payloads(image) if zxingcpp is not None else []

    if not payloads and have_zbar:
        payloads = _zbar_payloads(image)

    if not payloads and have_zbar:
        for region in _locate_qr_regions(image):
            for scale in (1.0, 1.5, 2.0, 3.0):
                candidate = (
                    region
                    if scale == 1.0
                    else cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                )
                payloads = _zbar_payloads(candidate)
                if payloads:
                    break
            if payloads:
                break

    # A small image gets the whole ladder whatever side it shows: upscaling it
    # is cheap, and it is the only route that reads a QR at that resolution.
    if not payloads and (thorough or _is_small(image)) and have_zbar:
        for scale in (1.5, 2.0):
            payloads = _zbar_payloads(
                cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            )
            if payloads:
                break

    if len(_PAYLOAD_CACHE) >= _PAYLOAD_CACHE_SIZE:
        _PAYLOAD_CACHE.pop(next(iter(_PAYLOAD_CACHE)))
    _PAYLOAD_CACHE[(digest, thorough)] = tuple(payloads)
    return payloads


def read_aadhaar_qr(
    image_bytes: bytes, thorough: bool = True
) -> tuple[AadhaarQR | None, str]:
    """
    Find and parse an Aadhaar Secure QR on a document image.

    Returns (parsed QR, note). A missing QR is not a finding: older Aadhaar
    cards carry none, and a QR outside the frame or out of focus is a capture
    problem rather than evidence about the document. `thorough` is passed to
    extract_qr_payloads.
    """
    payloads = extract_qr_payloads(image_bytes, thorough=thorough)
    if not payloads:
        return None, (
            "No QR code was read from this image. Older Aadhaar cards carry "
            "none; otherwise the photograph is the usual cause -- a dense QR "
            "that is out of focus, small in the frame, or crossed by glare will "
            "not decode. A close, steady photo of just the QR works best."
        )

    errors: list[str] = []
    for payload in payloads:
        try:
            return parse_secure_qr(payload), ""
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    return None, (
        f"{len(payloads)} QR code(s) were read but none was an Aadhaar Secure "
        f"QR ({'; '.join(errors[:2])})."
    )
