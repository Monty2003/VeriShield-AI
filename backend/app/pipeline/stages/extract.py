"""
Field extraction (Layer 3, second half).

OCR gives raw text; this turns it into typed, normalised fields with
provenance. Fields sourced from the MRZ are marked as such and trusted above
visually-read ones, because MRZ text is a fixed-width machine-readable band
with its own check digits, while the printed zone is subject to fonts,
glare, stamps and handwriting.

That provenance distinction is what later lets the pipeline compare the two
zones against each other -- the single most informative check on a passport.
"""

from __future__ import annotations

import re

from app.rules.mrz import MRZData, normalize_mrz_text, parse_mrz
from app.schemas.document import DocumentType, ExtractedFields, FieldConfidence
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

# Printed-zone date formats seen on Indian identity documents.
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{2})[/\-.](\d{2})[/\-.](\d{4})\b"), "%d/%m/%Y"),
    (re.compile(r"\b(\d{4})[/\-.](\d{2})[/\-.](\d{2})\b"), "%Y/%m/%d"),
]

_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
_AADHAAR_RE = re.compile(r"\b(\d{4})\s?(\d{4})\s?(\d{4})\b")


def _mrz_fields(mrz: MRZData) -> ExtractedFields:
    """Populate fields from parsed MRZ, marking every one as MRZ-sourced."""
    fields = ExtractedFields()

    def mk(value, raw: str) -> FieldConfidence:
        # Confidence 0.98, not 1.0: the MRZ layout is exact, but the OCR that
        # read it is not, and pretending otherwise would hide the one real
        # source of error in this path.
        return FieldConfidence(value=value, raw=raw, confidence=0.98, source="mrz")

    # Name fields come from line 1, which is filler-delimited and therefore
    # the first casualty of OCR. When the separators are gone the surname and
    # given names cannot be told apart, and mrz.name_fields_reliable is False.
    # We leave them unset rather than emit a confidently wrong name: cross-
    # document comparison would then report a mismatch against the same
    # person's other documents.
    if mrz.name_fields_reliable:
        if mrz.surname:
            fields.surname = mk(mrz.surname, mrz.surname)
        if mrz.given_names:
            fields.given_names = mk(mrz.given_names, mrz.given_names)
        if mrz.full_name:
            fields.full_name = mk(mrz.full_name, mrz.full_name)
    if mrz.document_number:
        fields.document_number = mk(mrz.document_number, mrz.document_number)
    if mrz.nationality:
        # Nationality sits at positions 10-12, which NO check digit covers, so
        # an OCR misread there is undetectable by validation. Measured on a
        # real Norwegian specimen: NOR was read as 'N0R' and every check digit
        # still passed. Lower confidence records that the value is unverified.
        fields.nationality = FieldConfidence(
            value=mrz.nationality,
            raw=mrz.nationality,
            confidence=0.75,
            source="mrz",
        )
    if mrz.issuing_country and mrz.name_fields_reliable:
        fields.issuing_country = mk(mrz.issuing_country, mrz.issuing_country)
    if mrz.date_of_birth:
        fields.date_of_birth = mk(mrz.date_of_birth, mrz.raw_line2[13:19])
    if mrz.date_of_expiry:
        fields.date_of_expiry = mk(mrz.date_of_expiry, mrz.raw_line2[21:27])
    if mrz.sex:
        fields.sex = mk(mrz.sex, mrz.sex)

    fields.mrz_line1 = FieldConfidence(
        value=mrz.raw_line1, raw=mrz.raw_line1, confidence=0.98, source="mrz"
    )
    fields.mrz_line2 = FieldConfidence(
        value=mrz.raw_line2, raw=mrz.raw_line2, confidence=0.98, source="mrz"
    )
    return fields


def _extract_pan(text: str, fields: ExtractedFields) -> list[Signal]:
    """Pull the PAN number out of printed text."""
    match = _PAN_RE.search(text.upper())
    if not match:
        return [
            signal(
                code="extract.pan.not_found",
                stage=Stage.EXTRACT,
                title="PAN number",
                status=SignalStatus.WARN,
                severity=Severity.HIGH,
                reason=(
                    "No value matching the PAN format (five letters, four digits, "
                    "one letter) was found in the extracted text."
                ),
            )
        ]

    fields.document_number = FieldConfidence(
        value=match.group(1), raw=match.group(1), confidence=0.9, source="ocr"
    )
    return [
        signal(
            code="extract.pan.found",
            stage=Stage.EXTRACT,
            title="PAN number",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            reason=f"Extracted PAN {match.group(1)} from the printed text.",
            evidence={"pan": match.group(1)},
        )
    ]


def _extract_aadhaar(text: str, fields: ExtractedFields) -> list[Signal]:
    """Pull the 12-digit Aadhaar number out of printed text."""
    match = _AADHAAR_RE.search(text)
    if not match:
        return [
            signal(
                code="extract.aadhaar.not_found",
                stage=Stage.EXTRACT,
                title="Aadhaar number",
                status=SignalStatus.WARN,
                severity=Severity.HIGH,
                reason="No 12-digit value in Aadhaar format was found in the text.",
            )
        ]

    number = "".join(match.groups())
    fields.document_number = FieldConfidence(
        value=number, raw=match.group(0), confidence=0.85, source="ocr"
    )
    return [
        signal(
            code="extract.aadhaar.found",
            stage=Stage.EXTRACT,
            title="Aadhaar number",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            # Deliberately masked. Aadhaar numbers should not be echoed in
            # logs, reports or API responses in full, and a verification tool
            # has no reason to be the place they leak from.
            reason=f"Extracted a 12-digit Aadhaar number ending {number[-4:]}.",
            evidence={"masked": f"XXXX XXXX {number[-4:]}"},
        )
    ]


def extract_fields(
    text: str, doc_type: DocumentType
) -> tuple[ExtractedFields, list[Signal], MRZData | None]:
    """
    Extract structured fields for the given document type.

    Returns the fields, the Signals describing extraction quality, and any
    parsed MRZ, which the validation layer needs directly.
    """
    fields = ExtractedFields()
    signals: list[Signal] = []
    mrz: MRZData | None = None

    if not text.strip():
        return (
            fields,
            [
                signal(
                    code="extract.no_text",
                    stage=Stage.EXTRACT,
                    title="Field extraction",
                    status=SignalStatus.ERROR,
                    severity=Severity.HIGH,
                    reason=(
                        "No text was available to extract fields from, so no "
                        "content checks could run on this document."
                    ),
                )
            ],
            None,
        )

    if doc_type in (DocumentType.PASSPORT, DocumentType.VISA):
        candidates = normalize_mrz_text(text)
        mrz = parse_mrz(candidates)
        if mrz:
            fields = _mrz_fields(mrz)
            signals.append(
                signal(
                    code="extract.mrz.parsed",
                    stage=Stage.EXTRACT,
                    title="MRZ extraction",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason=(
                        f"Parsed a machine-readable zone and recovered "
                        f"{len(fields.populated())} fields from it."
                    ),
                    evidence={"line1": mrz.raw_line1, "line2": mrz.raw_line2},
                )
            )
        else:
            signals.append(
                signal(
                    code="extract.mrz.not_found",
                    stage=Stage.EXTRACT,
                    title="MRZ extraction",
                    status=SignalStatus.WARN,
                    severity=Severity.HIGH,
                    reason=(
                        f"No machine-readable zone was found in the text. "
                        f"{len(candidates)} candidate line(s) were considered but "
                        f"none had the two 44-character lines a passport MRZ "
                        f"requires. A cropped scan is the usual cause."
                    ),
                    evidence={"candidates": candidates[:4]},
                )
            )

    elif doc_type == DocumentType.PAN:
        signals.extend(_extract_pan(text, fields))

    elif doc_type == DocumentType.AADHAAR:
        signals.extend(_extract_aadhaar(text, fields))

    else:
        signals.append(
            signal(
                code="extract.generic",
                stage=Stage.EXTRACT,
                title="Field extraction",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    f"No field extractor is implemented for document type "
                    f"'{doc_type.value}'. Only image-level checks were applied."
                ),
            )
        )

    return fields, signals, mrz
