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
from datetime import date

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

# Indian ID cards print a field LABEL and put the value on the following line,
# often with the Devanagari and English halves glued together ("4/Name",
# "Tr/Father's Name"). Matching the label and taking what follows is far more
# robust than locating the value by position, which shifts with every card
# layout and every OCR reading order.
_LABELLED_NAME = re.compile(r"(?:^|/)\s*(?:NAME|NAAM)\s*$", re.I | re.M)
_LABELLED_FATHER = re.compile(r"FATHER'?S?\s*(?:/\s*GUARDIAN'?S?\s*)?NAME", re.I)
# D[O0]B, not just DOB. OCR reads the letter O as a zero constantly -- the same
# substitution that turned Norway's NOR into N0R on a passport MRZ. Measured on
# a real Aadhaar card: the label came back as "f/D0B:04/12/2005", this pattern
# missed it, and extraction fell through to the first date on the card -- which
# was the ISSUE date. The case was then rejected for a date-of-birth mismatch
# against the same holder's PAN card, while their portraits matched.
_LABELLED_DOB = re.compile(r"DATE\s*OF\s*BIRTH|\bD[O0]B\b|जन्म", re.I)

# Dates that are definitely NOT a birth date. An Indian ID card carries issue
# dates, print dates and validity dates, any of which satisfies a bare date
# pattern. Excluding them matters as much as finding the right label, because
# the fallback path has no label to go on at all -- and an issue date returned
# as a birth date is indistinguishable downstream from the holder genuinely
# having two different ones.
_NOT_A_BIRTH_DATE = re.compile(r"ISSUE|ISSUED|PRINT|VALID|EXPIR|DOWNLOAD|GENERAT", re.I)

_DATE_ANY = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")

# A line that could be a person's name: letters, spaces and the punctuation
# names actually contain, at a length a name plausibly has.
_NAME_LINE = re.compile(r"^[A-Z][A-Z\s.']{2,48}$")

# Words that mark a line as part of the form rather than a value. Without this
# the extractor happily returns "FATHER'S NAME" as somebody's name, because
# structurally it looks exactly like one.
_LABEL_WORDS = {
    "NAME", "FATHER", "MOTHER", "GUARDIAN", "SIGNATURE", "DATE", "BIRTH",
    "INCOME", "TAX", "DEPARTMENT", "GOVT", "GOVERNMENT", "INDIA", "PERMANENT",
    "ACCOUNT", "NUMBER", "CARD", "MALE", "FEMALE", "ADDRESS", "AADHAAR",
    "UNIQUE", "IDENTIFICATION", "AUTHORITY", "ROLL", "SCHOOL", "BOARD",
    "CERTIFICATE", "EXAMINATION", "SUBJECT", "GRADE", "TOTAL", "THEORY",
}


def _value_after_label(lines: list[str], label: re.Pattern[str]) -> str | None:
    """
    Return the first plausible value printed after a matching field label.

    Scans the next few lines rather than only the next one: OCR interleaves the
    two halves of a bilingual label and frequently emits a stray fragment
    between a label and its value.
    """
    for i, line in enumerate(lines):
        if not label.search(line):
            continue
        for candidate in lines[i + 1 : i + 4]:
            value = " ".join(candidate.split())
            if not _NAME_LINE.match(value):
                continue
            if set(re.findall(r"[A-Z]+", value.upper())) & _LABEL_WORDS:
                continue  # another label, not a value
            return value
    return None


def _first_date(lines: list[str], near: re.Pattern[str] | None = None) -> date | None:
    """
    Find a date, preferring one printed near a date-of-birth label.

    Preference matters: an Indian ID card carries several dates -- issue date,
    print date, a signature date -- and taking the first one found would
    routinely record the wrong one as the holder's birth date.
    """

    def parse(text: str) -> date | None:
        match = _DATE_ANY.search(text)
        if not match:
            return None
        day, month, year = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    if near is not None:
        for i, line in enumerate(lines):
            if near.search(line):
                for candidate in lines[i : i + 4]:
                    if _NOT_A_BIRTH_DATE.search(candidate):
                        continue
                    parsed = parse(candidate)
                    if parsed:
                        return parsed

    # Fallback: the first date that is not explicitly something else. Without
    # the exclusion this returns an issue date whenever the birth-date label
    # was misread, which is indistinguishable downstream from the holder
    # genuinely having two different birth dates.
    for line in lines:
        if _NOT_A_BIRTH_DATE.search(line):
            continue
        parsed = parse(line)
        if parsed:
            return parsed
    return None


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

    # The holder's name is what makes a PAN card self-checking: the number's
    # fifth character encodes the surname initial, so without the name that
    # cross-check cannot run and the card is only format-validated.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = _value_after_label(lines, _LABELLED_NAME)
    if name:
        fields.full_name = FieldConfidence(
            value=name, raw=name, confidence=0.8, source="ocr"
        )
    father = _value_after_label(lines, _LABELLED_FATHER)
    if father:
        fields.father_name = FieldConfidence(
            value=father, raw=father, confidence=0.8, source="ocr"
        )
    dob = _first_date(lines, _LABELLED_DOB)
    if dob:
        fields.date_of_birth = FieldConfidence(
            value=dob, raw=dob.isoformat(), confidence=0.8, source="ocr"
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


def _select_aadhaar_candidate(candidates: list[str]) -> tuple[str, bool]:
    """
    Choose which 12-digit run is the Aadhaar number.

    An Aadhaar card carries more than one long digit sequence -- the Virtual
    ID, the enrolment number, and whatever OCR reconstructs by joining
    fragments across a line break. Taking the first match found is wrong, and
    was measured to be wrong on a real card: the first candidate failed the
    checksum while the actual Aadhaar number, found second, passed. That
    document was being REJECTED as fraudulent.

    So the checksum is used to DISAMBIGUATE, not only to judge. Among several
    candidates, one that satisfies Verhoeff is almost certainly the real
    number: a wrong run has a one-in-ten chance of passing by accident.

    The critical restraint: this must not become a way of always finding a
    valid number. If there is only ONE candidate it is returned as-is, pass or
    fail, because that is the document's actual claim and suppressing a failure
    would delete the fraud check entirely. Preference applies only when there
    is a genuine choice to make.

    Returns (chosen number, whether disambiguation was needed).
    """
    from app.rules.aadhaar import verhoeff_checksum

    unique = list(dict.fromkeys(candidates))
    if len(unique) <= 1:
        return unique[0], False

    valid = [c for c in unique if verhoeff_checksum(c) == 0]
    if len(valid) == 1:
        return valid[0], True
    if valid:
        # Several validate. Nothing distinguishes them, so take the first and
        # let the reviewer see that the document was ambiguous.
        return valid[0], True
    return unique[0], True


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

    candidates = ["".join(m) for m in _AADHAAR_RE.findall(text)]
    number, disambiguated = _select_aadhaar_candidate(candidates)

    fields.document_number = FieldConfidence(
        value=number,
        raw=match.group(0),
        # Lower confidence when several candidates competed: the checksum
        # picked one, but the document was genuinely ambiguous about which
        # number it was presenting.
        confidence=0.75 if disambiguated else 0.85,
        source="ocr",
    )

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    dob = _first_date(lines, _LABELLED_DOB)
    if dob:
        fields.date_of_birth = FieldConfidence(
            value=dob, raw=dob.isoformat(), confidence=0.75, source="ocr"
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
            reason=(
                f"Extracted a 12-digit Aadhaar number ending {number[-4:]}."
                + (
                    f" The document contained {len(set(candidates))} different "
                    f"12-digit sequences -- Virtual ID and enrolment numbers look "
                    f"the same to a pattern match -- and the checksum was used to "
                    f"identify which one is the Aadhaar number."
                    if disambiguated
                    else ""
                )
            ),
            evidence={
                "masked": f"XXXX XXXX {number[-4:]}",
                "candidates_seen": len(set(candidates)),
                "disambiguated_by_checksum": disambiguated,
            },
        )
    ]


def extract_fields(
    text: str, doc_type: DocumentType, side: str = "unknown"
) -> tuple[ExtractedFields, list[Signal], MRZData | None]:
    """
    Extract structured fields for the given document type.

    Returns the fields, the Signals describing extraction quality, and any
    parsed MRZ, which the validation layer needs directly.
    """
    fields = ExtractedFields()
    signals: list[Signal] = []
    mrz: MRZData | None = None

    if side == "back":
        # The reverse of a card holds a return address or grading notes, never
        # the holder's details. Reporting "PAN number not found" here would
        # describe a correct photograph of a real document as defective, and
        # send the reviewer looking for a problem that does not exist.
        return (
            fields,
            [
                signal(
                    code="extract.reverse_side",
                    stage=Stage.EXTRACT,
                    title="Field extraction",
                    status=SignalStatus.SKIP,
                    severity=Severity.INFO,
                    reason=(
                        f"This image shows the reverse of the "
                        f"{doc_type.value.replace('_', ' ')}, which carries no "
                        f"identity fields by design. Nothing was extracted, and "
                        f"nothing was expected to be."
                    ),
                )
            ],
            None,
        )

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

    elif doc_type == DocumentType.CERTIFICATE:
        # A marksheet's verifiable content is the relationship between values
        # in its table, not a fixed field set, so the rulebook works from the
        # text directly.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        name = _value_after_label(lines, re.compile(r"CERTIFY\s*THAT", re.I))
        if name:
            fields.full_name = FieldConfidence(
                value=name, raw=name, confidence=0.75, source="ocr"
            )
        signals.append(
            signal(
                code="extract.certificate.text",
                stage=Stage.EXTRACT,
                title="Certificate content",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"Captured {len(lines)} lines of certificate text for rule "
                    f"validation" + (f"; candidate name {name!r}." if name else ".")
                ),
            )
        )

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

    # Preserved for rulebooks whose evidence lives in the text as a whole.
    fields.raw_text = FieldConfidence(
        value=text, raw=text, confidence=1.0, source="ocr"
    )
    return fields, signals, mrz
