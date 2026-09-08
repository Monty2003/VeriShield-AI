"""
PAN (Permanent Account Number) validation rules (Layer 4).

A PAN has no published check digit -- the tenth character is one, but the
Income Tax Department has never released the algorithm, so it cannot be
verified offline. Claiming otherwise would be inventing evidence.

What CAN be verified is structure, and one genuinely strong internal
consistency check: the fifth character of a PAN is the first letter of the
holder's surname. That means the number and the printed name must agree with
each other, and a forger who edits the name without reissuing the number --
or invents a number to sit under a real name -- breaks that agreement.

It is the same idea as an MRZ check digit, in a weaker form: the document
carries redundant information about itself, and redundancy is what makes
tampering detectable without any external database.
"""

from __future__ import annotations

import re

from app.schemas.document import ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

PAN_LENGTH = 10
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Fourth character: the category of the holder. Anything outside this set means
# the value is not a PAN as issued, whatever else it looks like.
HOLDER_TYPES: dict[str, str] = {
    "P": "individual",
    "C": "company",
    "H": "Hindu Undivided Family",
    "F": "firm or limited liability partnership",
    "A": "association of persons",
    "T": "trust",
    "B": "body of individuals",
    "L": "local authority",
    "J": "artificial juridical person",
    "G": "government",
}

# Honorifics that precede a name and must not be mistaken for the surname.
_HONORIFICS = {
    "MR", "MRS", "MS", "MISS", "DR", "PROF",
    "SHRI", "SHRIMATI", "SMT", "SRI", "KUMARI", "KM",
}


def normalize_pan(value: str) -> str:
    """Strip spacing and case so a PAN can be compared and pattern-matched."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()


def is_structurally_valid(pan: str) -> bool:
    """Five letters, four digits, one letter, with a known holder type."""
    pan = normalize_pan(pan)
    return bool(PAN_PATTERN.match(pan)) and pan[3] in HOLDER_TYPES


def surname_initial(full_name: str) -> str | None:
    """
    Best guess at the surname's first letter from a printed name.

    Indian PAN cards print names given-name-first, so the surname is the LAST
    token. This is a convention rather than a rule, which is why a mismatch
    here is reported as a warning to look at rather than a finding -- some
    cards carry a single mononym, and some holders' registered surname differs
    from what is printed.
    """
    cleaned = "".join(c if c.isalpha() or c.isspace() else " " for c in full_name.upper())
    tokens = [t for t in cleaned.split() if t and t not in _HONORIFICS]
    if not tokens:
        return None
    return tokens[-1][0]


def validate_pan(_mrz=None, fields: ExtractedFields | None = None) -> list[Signal]:
    """Run the PAN rulebook. PAN cards have no MRZ, so that argument is ignored."""
    if fields is None or not fields.document_number.present:
        return [
            signal(
                code="pan.number.not_found",
                stage=Stage.VALIDATE,
                title="PAN number",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    "No PAN number could be read from this document, so none of "
                    "the structural checks could run and nothing about the number "
                    "was validated. Usually a blurred or cropped image rather "
                    "than a sign of forgery."
                ),
            )
        ]

    pan = normalize_pan(fields.document_number.value)
    signals: list[Signal] = []

    # --- structure ---
    if len(pan) != PAN_LENGTH or not PAN_PATTERN.match(pan):
        return [
            signal(
                code="pan.number.malformed",
                stage=Stage.VALIDATE,
                title="PAN number format",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    f"{pan!r} does not match the PAN format of five letters, four "
                    f"digits and one letter. Either the image was misread or this "
                    f"is not a PAN number."
                ),
                evidence={"value": pan, "expected_format": "AAAAA9999A"},
            )
        ]

    signals.append(
        signal(
            code="pan.number.format_valid",
            stage=Stage.VALIDATE,
            title="PAN number format",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            reason=f"{pan} matches the PAN format (five letters, four digits, one letter).",
            evidence={"pan": pan},
        )
    )

    # --- holder type (4th character) ---
    holder_char = pan[3]
    if holder_char in HOLDER_TYPES:
        signals.append(
            signal(
                code="pan.holder_type.valid",
                stage=Stage.VALIDATE,
                title="PAN holder category",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"The fourth character '{holder_char}' encodes a valid holder "
                    f"category: {HOLDER_TYPES[holder_char]}."
                ),
                evidence={"character": holder_char, "category": HOLDER_TYPES[holder_char]},
            )
        )
    else:
        signals.append(
            signal(
                code="pan.holder_type.invalid",
                stage=Stage.VALIDATE,
                title="PAN holder category",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                confidence=0.85,
                reason=(
                    f"The fourth character of a PAN encodes the holder category, "
                    f"and '{holder_char}' is not one the Income Tax Department "
                    f"issues. Valid values are "
                    f"{', '.join(sorted(HOLDER_TYPES))}. Either the character was "
                    f"misread or the number is fabricated."
                ),
                evidence={"character": holder_char, "valid": sorted(HOLDER_TYPES)},
            )
        )

    # --- surname initial (5th character) vs the printed name ---
    #
    # This is the only redundancy a PAN card carries: the number encodes
    # something the card also prints. That redundancy is what makes an edit
    # detectable without consulting any external record.
    expected_initial = pan[4]
    if fields.full_name.present:
        actual = surname_initial(str(fields.full_name.value))
        if actual is None:
            signals.append(
                signal(
                    code="pan.surname_initial.no_name",
                    stage=Stage.VALIDATE,
                    title="PAN surname initial",
                    status=SignalStatus.SKIP,
                    severity=Severity.INFO,
                    reason="No usable name was read, so the surname initial could not be cross-checked.",
                )
            )
        elif actual == expected_initial:
            signals.append(
                signal(
                    code="pan.surname_initial.match",
                    stage=Stage.VALIDATE,
                    title="PAN surname initial",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    confidence=0.85,
                    reason=(
                        f"The fifth character of the PAN is '{expected_initial}', "
                        f"which matches the first letter of the surname printed on "
                        f"the card. The number and the name agree with each other."
                    ),
                    evidence={
                        "pan_fifth_character": expected_initial,
                        "surname_initial": actual,
                    },
                )
            )
        else:
            signals.append(
                signal(
                    code="pan.surname_initial.mismatch",
                    stage=Stage.VALIDATE,
                    title="PAN surname initial",
                    status=SignalStatus.FAIL,
                    severity=Severity.HIGH,
                    # Not CRITICAL, and not certain. The surname-last convention
                    # holds for most Indian PAN cards but not all: mononyms,
                    # reordered names and registered surnames that differ from
                    # the printed form all produce an honest mismatch. It is a
                    # strong reason to look, not a determination.
                    confidence=0.6,
                    reason=(
                        f"The fifth character of a PAN is the first letter of the "
                        f"holder's surname. This PAN has '{expected_initial}', but "
                        f"the surname printed on the card begins with '{actual}'. "
                        f"That disagreement is what an edited name or a fabricated "
                        f"number looks like -- though it also occurs legitimately "
                        f"when the printed name is ordered differently from the "
                        f"registered one."
                    ),
                    evidence={
                        "pan_fifth_character": expected_initial,
                        "surname_initial": actual,
                        "name_read": str(fields.full_name.value),
                    },
                )
            )
    else:
        signals.append(
            signal(
                code="pan.surname_initial.no_name",
                stage=Stage.VALIDATE,
                title="PAN surname initial",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "The holder's name was not read, so the PAN's encoded surname "
                    "initial could not be cross-checked against it. This is the "
                    "strongest offline check a PAN card supports, so a clearer "
                    "image of the name would materially strengthen this assessment."
                ),
            )
        )

    # --- the tenth character ---
    signals.append(
        signal(
            code="pan.check_digit.unverifiable",
            stage=Stage.VALIDATE,
            title="PAN check digit",
            status=SignalStatus.SKIP,
            severity=Severity.INFO,
            reason=(
                "The tenth character of a PAN is a check digit, but the Income "
                "Tax Department has never published the algorithm, so it cannot "
                "be verified offline. Unlike a passport MRZ, a PAN number cannot "
                "be proved self-consistent by arithmetic alone -- confirming it "
                "requires the department's own verification service."
            ),
            evidence={"check_character": pan[9]},
        )
    )

    return signals
