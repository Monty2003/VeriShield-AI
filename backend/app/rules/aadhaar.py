"""
Aadhaar validation rules (Layer 4).

The Verhoeff checksum is to Aadhaar what the ICAO check digits are to a
passport: real arithmetic, not a model's opinion. A number invented at random
has a one-in-ten chance of passing, so a failure is close to conclusive
evidence that the number was fabricated or misread -- with no training data
and no image analysis involved.

Privacy note, load-bearing throughout this module: an Aadhaar number is never
echoed in full. Every reason string and evidence payload carries only the last
four digits. A verification tool has no business being the place a national
identity number leaks from, and reasons are written to logs and API responses
that outlive the request.
"""

from __future__ import annotations

import re
from datetime import date

from app.schemas.document import ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

AADHAAR_LENGTH = 12

# UIDAI does not issue numbers beginning 0 or 1: those ranges are reserved so
# an Aadhaar can never be confused with a landline or a legacy identifier.
RESERVED_LEADING_DIGITS = ("0", "1")

_DIGITS_ONLY = re.compile(r"\D")

# --- Verhoeff tables (Dihedral group D5) -----------------------------------
# Verhoeff catches every single-digit error and every adjacent transposition,
# which is exactly the error profile of hand-typed and OCR-read numbers. That
# is why UIDAI chose it, and why it is worth implementing exactly rather than
# substituting a simpler mod-10 scheme.

_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_checksum(number: str) -> int:
    """
    Compute the Verhoeff checksum of a digit string.

    Zero means the number (including its final check digit) is self-consistent.
    """
    checksum = 0
    for position, digit in enumerate(reversed(number)):
        checksum = _D[checksum][_P[position % 8][int(digit)]]
    return checksum


def verhoeff_check_digit(payload: str) -> int:
    """Compute the check digit that would make `payload` valid."""
    checksum = 0
    for position, digit in enumerate(reversed(payload)):
        checksum = _D[checksum][_P[(position + 1) % 8][int(digit)]]
    return _INV[checksum]


def is_valid_aadhaar(number: str) -> bool:
    """Full structural and checksum validation of a 12-digit Aadhaar number."""
    digits = _DIGITS_ONLY.sub("", number)
    if len(digits) != AADHAAR_LENGTH:
        return False
    if digits[0] in RESERVED_LEADING_DIGITS:
        return False
    return verhoeff_checksum(digits) == 0


def mask(number: str) -> str:
    """Render an Aadhaar number safe to log or display."""
    digits = _DIGITS_ONLY.sub("", number)
    if len(digits) < 4:
        return "XXXX XXXX XXXX"
    return f"XXXX XXXX {digits[-4:]}"


def validate_aadhaar_qr(image_bytes: bytes, fields: ExtractedFields) -> list[Signal]:
    """
    Check the card against its own Secure QR, when it has one.

    Separate from validate_aadhaar because it needs the IMAGE, not just the
    extracted fields -- the QR has to be located and decoded from pixels.
    """
    from app.rules.aadhaar_qr import read_aadhaar_qr
    from app.rules.aadhaar_qr_validate import validate_against_qr

    qr, note = read_aadhaar_qr(image_bytes)
    if qr is None:
        return [
            signal(
                code="aadhaar.qr.absent",
                stage=Stage.DATABASE,
                title="Aadhaar Secure QR",
                # SKIP, not a finding. Older cards carry no QR at all, and a
                # dense QR photographed at an angle often will not decode --
                # neither says anything about the document.
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    note
                    + " Without it, the name, date of birth and address on this "
                    "card cannot be checked against anything: none of them carries "
                    "a checksum."
                ),
            )
        ]
    return validate_against_qr(qr, fields)


def validate_aadhaar(
    _mrz=None, fields: ExtractedFields | None = None, today: date | None = None
) -> list[Signal]:
    """
    Run the Aadhaar rulebook.

    Signature matches the registry's Validator protocol; Aadhaar cards have no
    MRZ, so the first argument is ignored.
    """
    today = today or date.today()

    if fields is None or not fields.document_number.present:
        return [
            signal(
                code="aadhaar.number.not_found",
                stage=Stage.VALIDATE,
                title="Aadhaar number",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                # Blocking: with no number there is nothing to verify. The card
                # is not accused of anything, but neither has it been checked.
                blocking=True,
                reason=(
                    "No Aadhaar number could be read from this document, so the "
                    "checksum could not be verified and nothing about the number "
                    "was validated. This is usually a blurred or cropped image "
                    "rather than a sign of forgery."
                ),
            )
        ]

    raw = str(fields.document_number.value)
    digits = _DIGITS_ONLY.sub("", raw)
    signals: list[Signal] = []

    # --- length ---
    if len(digits) != AADHAAR_LENGTH:
        return [
            signal(
                code="aadhaar.number.malformed",
                stage=Stage.VALIDATE,
                title="Aadhaar number format",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    f"The number read has {len(digits)} digits; an Aadhaar number "
                    f"has exactly {AADHAAR_LENGTH}. Either the image was misread "
                    f"or this is not an Aadhaar number."
                ),
                evidence={"digits_found": len(digits), "expected": AADHAAR_LENGTH},
            )
        ]

    # --- reserved range ---
    if digits[0] in RESERVED_LEADING_DIGITS:
        signals.append(
            signal(
                code="aadhaar.number.reserved_range",
                stage=Stage.VALIDATE,
                title="Aadhaar number range",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                confidence=0.9,
                reason=(
                    f"The number begins with {digits[0]}. UIDAI does not issue "
                    f"Aadhaar numbers starting with 0 or 1 -- those ranges are "
                    f"reserved -- so this value cannot be a genuine Aadhaar "
                    f"number as read."
                ),
                evidence={"masked": mask(digits), "leading_digit": digits[0]},
            )
        )

    # --- Verhoeff checksum: the real check ---
    checksum = verhoeff_checksum(digits)
    if checksum == 0:
        signals.append(
            signal(
                code="aadhaar.checksum.valid",
                stage=Stage.VALIDATE,
                title="Aadhaar checksum (Verhoeff)",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"The Aadhaar number ending {digits[-4:]} satisfies the "
                    f"Verhoeff checksum. A randomly invented number passes this "
                    f"only about one time in ten, so the number is internally "
                    f"consistent."
                ),
                evidence={"masked": mask(digits), "verhoeff": "valid"},
            )
        )
    else:
        expected = verhoeff_check_digit(digits[:-1])
        signals.append(
            signal(
                code="aadhaar.checksum.invalid",
                stage=Stage.VALIDATE,
                title="Aadhaar checksum (Verhoeff)",
                status=SignalStatus.FAIL,
                severity=Severity.CRITICAL,
                # High but not absolute: the arithmetic is certain, but it was
                # performed on characters OCR read from a photograph. A single
                # misread digit produces exactly this result, which is why the
                # reason says so instead of asserting fraud.
                confidence=0.9,
                reason=(
                    f"The Aadhaar number ending {digits[-4:]} fails the Verhoeff "
                    f"checksum -- its final digit is {digits[-1]} where the rest "
                    f"of the number requires {expected}. Genuine Aadhaar numbers "
                    f"always satisfy this. Either a digit was misread from the "
                    f"image, or the number is fabricated."
                ),
                evidence={
                    "masked": mask(digits),
                    "stated_check_digit": digits[-1],
                    "computed_check_digit": expected,
                },
            )
        )

    # --- repeated-digit sanity ---
    # Fabricated numbers are often lazy: 1111 1111 1111, or a short run
    # repeated. Real Aadhaar numbers are randomly assigned, so this pattern is
    # vanishingly rare and worth surfacing even when the checksum happens to pass.
    if len(set(digits)) <= 2:
        signals.append(
            signal(
                code="aadhaar.number.low_entropy",
                stage=Stage.VALIDATE,
                title="Aadhaar number pattern",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                confidence=0.7,
                reason=(
                    f"The number ending {digits[-4:]} uses only "
                    f"{len(set(digits))} distinct digits. UIDAI assigns numbers "
                    f"randomly, so such a pattern is extremely unlikely in a "
                    f"genuine number and is common in fabricated ones."
                ),
                evidence={"masked": mask(digits), "distinct_digits": len(set(digits))},
            )
        )

    # --- date of birth plausibility, when the card gave us one ---
    if fields.date_of_birth.present and isinstance(fields.date_of_birth.value, date):
        dob = fields.date_of_birth.value
        if dob > today:
            signals.append(
                signal(
                    code="aadhaar.dob.future",
                    stage=Stage.VALIDATE,
                    title="Date of birth",
                    status=SignalStatus.FAIL,
                    severity=Severity.HIGH,
                    blocking=True,
                    reason=(
                        f"The date of birth {dob.isoformat()} is in the future and "
                        f"cannot be correct."
                    ),
                    evidence={"dob": dob.isoformat()},
                )
            )
        else:
            age = (today - dob).days / 365.25
            if age > 120:
                signals.append(
                    signal(
                        code="aadhaar.dob.implausible",
                        stage=Stage.VALIDATE,
                        title="Date of birth",
                        status=SignalStatus.WARN,
                        severity=Severity.MEDIUM,
                        reason=(
                            f"The date of birth {dob.isoformat()} implies an age of "
                            f"{age:.0f} years, which is beyond any plausible value. "
                            f"The year was probably misread."
                        ),
                        evidence={"dob": dob.isoformat(), "age_years": round(age, 1)},
                    )
                )
            else:
                signals.append(
                    signal(
                        code="aadhaar.dob.plausible",
                        stage=Stage.VALIDATE,
                        title="Date of birth",
                        status=SignalStatus.PASS,
                        severity=Severity.INFO,
                        reason=(
                            f"Date of birth {dob.isoformat()} gives an age of "
                            f"{age:.0f} years, which is plausible."
                        ),
                        evidence={"dob": dob.isoformat()},
                    )
                )

    return signals
