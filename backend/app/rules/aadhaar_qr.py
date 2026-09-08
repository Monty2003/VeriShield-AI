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


def extract_qr_payloads(image_bytes: bytes) -> list[bytes]:
    """
    Find and read every QR code on a document image.

    Tries progressive upscaling: the Aadhaar Secure QR is dense (version 12 or
    higher) and a phone photograph of a card often renders its modules just
    below what a decoder resolves at native size.
    """
    import cv2

    from app.core.imaging import decode_image

    image = decode_image(image_bytes)
    payloads: list[bytes] = []

    try:
        from pyzbar.pyzbar import decode as zbar_decode
    except ImportError:
        return payloads

    for scale in (1.0, 1.5, 2.0):
        candidate = (
            image
            if scale == 1.0
            else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        )
        gray = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
        for result in zbar_decode(gray):
            if result.data and result.data not in payloads:
                payloads.append(result.data)
        if payloads:
            break

    return payloads


def read_aadhaar_qr(image_bytes: bytes) -> tuple[AadhaarQR | None, str]:
    """
    Find and parse an Aadhaar Secure QR on a document image.

    Returns (parsed QR, note). A missing QR is not a finding: older Aadhaar
    cards carry none, and a QR outside the frame or out of focus is a capture
    problem rather than evidence about the document.
    """
    payloads = extract_qr_payloads(image_bytes)
    if not payloads:
        return None, (
            "No QR code was read from this image. Older Aadhaar cards carry "
            "none, and a dense QR photographed at an angle or slightly out of "
            "focus often will not decode."
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
