"""
ICAO 9303 Machine Readable Zone parsing and validation.

This module is the highest-value validator in VeriShield, and the reason the
passport vertical slice was built first: MRZ check digits are real arithmetic,
not a model's opinion. If a forger edits a passport number, date of birth, or
expiry date in the visual zone but does not recompute the MRZ check digits,
this catches it deterministically -- confidence 1.0, no training data needed.

Currently implements TD3 (passport, 2x44). TD1/TD2/MRV share the same check
digit algorithm and slot in via the same fixed-offset approach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# Weights cycle 7-3-1 across every MRZ check digit computation (ICAO 9303 pt3).
_WEIGHTS = (7, 3, 1)

# Filler character. Pads every variable-length MRZ field to fixed width.
FILLER = "<"

TD3_LINE_LENGTH = 44
TD3_LINE_COUNT = 2


def char_value(ch: str) -> int:
    """
    Numeric value of an MRZ character.

    Digits are themselves, A-Z are 10-35, and the filler is 0.
    Anything else is not valid MRZ input.
    """
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    if ch == FILLER:
        return 0
    raise ValueError(f"invalid MRZ character: {ch!r}")


def check_digit(data: str) -> int:
    """ICAO 9303 check digit: weighted sum of character values, mod 10."""
    total = 0
    for i, ch in enumerate(data):
        total += char_value(ch) * _WEIGHTS[i % 3]
    return total % 10


def verify_check_digit(data: str, digit: str) -> bool:
    """
    Verify a field against its stated check digit.

    A filler in the check digit position is treated as 0, which is what
    issuers use when the field itself is entirely filler (commonly the
    optional personal number).
    """
    if digit == FILLER:
        digit = "0"
    if not digit.isdigit():
        return False
    try:
        return check_digit(data) == int(digit)
    except ValueError:
        return False


def parse_mrz_date(yymmdd: str, *, is_expiry: bool = False) -> date | None:
    """
    Parse a 6-digit YYMMDD MRZ date into a real date.

    MRZ carries only two year digits, so the century must be inferred.
    Expiry dates are always taken as 20xx -- a passport that expired in the
    1900s is not something a verification system will meet in practice.
    Birth dates use a sliding window against today: a two-digit year ahead of
    the current year must belong to the previous century.
    """
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None

    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])

    if is_expiry:
        year = 2000 + yy
    else:
        current_yy = date.today().year % 100
        year = 1900 + yy if yy > current_yy else 2000 + yy

    try:
        return date(year, mm, dd)
    except ValueError:
        # Real dates only -- 30 February in an MRZ is itself a finding.
        return None


def parse_mrz_name(name_field: str) -> tuple[str, str, str]:
    """
    Split the MRZ name field into (surname, given_names, full_name).

    Format is SURNAME<<GIVEN<MIDDLE: a DOUBLE filler separates surname from
    given names, a single filler separates words within each part.

    The surname/given split therefore rests entirely on that double filler
    surviving OCR, and it frequently does not. On a real Russian specimen,
    PaddleOCR returned 'SMIRNOVA<VALENTINA' -- one filler where the document
    has two. Splitting on what is left would silently produce the surname
    'SMIRNOVA VALENTINA'.

    So when the double filler is absent we return the full name and leave the
    split empty. The full name is still correct and is what cross-document
    comparison actually uses, since that matching is order-insensitive. An
    empty surname is a known gap; a wrong one is a false mismatch against the
    same person's other documents.
    """
    cleaned = name_field.rstrip(FILLER)
    full = re.sub(r"\s+", " ", cleaned.replace(FILLER, " ")).strip()

    if FILLER * 2 not in cleaned:
        return "", "", full

    head, tail = cleaned.split(FILLER * 2, 1)
    surname = re.sub(r"\s+", " ", head.replace(FILLER, " ")).strip()
    given = re.sub(r"\s+", " ", tail.replace(FILLER, " ")).strip()
    return surname, given, full


@dataclass
class CheckResult:
    """One check digit verification, kept with its inputs for the evidence panel."""

    field_name: str
    raw_value: str
    stated_digit: str
    computed_digit: int
    valid: bool


# Positions in TD3 line 2 that carry data protected by a check digit.
# Everything from 0 to 27 inclusive: document number + its digit, nationality,
# date of birth + its digit, sex, date of expiry + its digit.
PROTECTED_PREFIX_END = 28

# Check digits whose inputs lie entirely within the protected prefix, and so
# remain trustworthy even when the tail of the line had to be reconstructed.
PREFIX_CHECKS = frozenset({"document_number", "date_of_birth", "date_of_expiry"})

# Nationality occupies positions 10-12 and is covered by NO check digit in
# TD3 -- the composite spans 0-9, 13-19 and 21-42, skipping it. An OCR misread
# there (O read as 0 is the classic one) is therefore invisible to checksum
# validation, and callers must not treat a passing MRZ as confirmation of it.
UNPROTECTED_FIELDS = ("nationality",)


@dataclass
class MRZData:
    """Parsed TD3 MRZ with every check digit result retained."""

    document_code: str = ""
    issuing_country: str = ""
    surname: str = ""
    given_names: str = ""
    # Kept separately: the full name survives OCR filler loss even when the
    # surname/given boundary does not.
    name_full: str = ""
    document_number: str = ""
    nationality: str = ""
    date_of_birth: date | None = None
    sex: str = ""
    date_of_expiry: date | None = None
    personal_number: str = ""

    raw_line1: str = ""
    raw_line2: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    # True when line 2 was rebuilt from a short OCR reading by re-inflating a
    # dropped filler run. When set, only PREFIX_CHECKS carry real evidential
    # weight -- see `trustworthy_checks`.
    line2_reconstructed: bool = False
    reconstruction_note: str = ""

    # True when line 1 lost its filler separators and the name fields could
    # not be split reliably.
    line1_unreliable: bool = False

    @property
    def trustworthy_checks(self) -> list[CheckResult]:
        """
        Check digits whose result is real evidence.

        When line 2 was reconstructed, the personal-number and composite
        digits validate TRIVIALLY rather than meaningfully: the filler we
        inserted has character value 0, exactly like the personal check digit
        it displaced, so those two sums come out right no matter what the
        document actually said. Reporting them as passes would be presenting
        an artefact of our own repair as evidence about the document.

        The prefix checks are unaffected -- positions 0-27 are untouched by
        reconstruction -- so they remain genuine.
        """
        if not self.line2_reconstructed:
            return self.checks
        return [c for c in self.checks if c.field_name in PREFIX_CHECKS]

    @property
    def all_checks_valid(self) -> bool:
        """True only if every check we can actually trust passed."""
        trusted = self.trustworthy_checks
        return bool(trusted) and all(c.valid for c in trusted)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.trustworthy_checks if not c.valid]

    @property
    def full_name(self) -> str:
        """Prefer the explicit split; fall back to the unsplit reading."""
        joined = f"{self.given_names} {self.surname}".strip()
        return joined or self.name_full

    @property
    def name_fields_reliable(self) -> bool:
        """
        Whether the surname/given-name split can be believed.

        Line 1 encodes the boundary between surname and given names purely as
        a double filler. OCR engines drop filler runs, and when they do the
        whole name collapses into one string with no recoverable structure --
        measured on a real Norwegian specimen, which came back as
        'PNOROESTENBYENAASAMUNDSPECIMEN'.

        Reporting that as a surname at high confidence would be worse than
        reporting nothing: it flows into cross-document name comparison and
        manufactures a mismatch against the same person's other documents.
        """
        return not self.line1_unreliable

    @property
    def is_expired(self) -> bool | None:
        if self.date_of_expiry is None:
            return None
        return self.date_of_expiry < date.today()


def normalize_mrz_text(text: str) -> list[str]:
    """
    Pull candidate MRZ lines out of raw OCR output.

    OCR on the MRZ band is noisy in predictable ways: O/0 and I/1 confusion,
    stray spaces, and lowercase leakage. We uppercase, strip spaces, and keep
    only lines that look structurally like MRZ -- long, and dominated by the
    MRZ alphabet.
    """
    candidates: list[str] = []
    for line in text.splitlines():
        s = line.upper().replace(" ", "").strip()
        if len(s) < 30:
            continue
        allowed = sum(1 for c in s if c.isalnum() or c == FILLER)
        if allowed / len(s) >= 0.9:
            candidates.append(s)
    return candidates


def _pad_or_trim(line: str, length: int) -> str:
    """Force a line to exact MRZ width so fixed offsets stay meaningful."""
    return line[:length] if len(line) >= length else line.ljust(length, FILLER)


# The shortest line 2 worth trying to rebuild. Everything before position 28
# must be present, or the fields we would recover are not the fields the
# document states.
MIN_RECONSTRUCTABLE_LINE2 = 30


def reconstruct_line2(line: str) -> tuple[str, bool, str]:
    """
    Rebuild a short line 2 by re-inflating a dropped filler run.

    Why this is needed: general-purpose OCR engines are not trained on the MRZ
    filler character and routinely drop or compress runs of it. Measured on
    real specimen passports, PaddleOCR returned 33-35 characters where the
    standard requires 44 -- with the missing characters coming entirely from
    the personal-number filler run. The data itself was intact.

    Why it is safe for the PREFIX: positions 0-27 hold the document number,
    nationality, date of birth, sex, expiry and three check digits. Padding
    happens after position 27, so none of those move, and their check digits
    remain genuine evidence.

    Why it is NOT safe for the TAIL, and why the caller is told: the filler we
    insert has character value 0, and so does the personal check digit it
    displaces. The personal-number and composite sums therefore come out
    correct REGARDLESS of what the document said. Those two checks become
    artefacts of this repair, not findings about the document -- which is why
    `MRZData.trustworthy_checks` drops them once this has run.

    Returns (line, was_reconstructed, note).
    """
    line = line.upper().replace(" ", "")

    if len(line) >= TD3_LINE_LENGTH:
        return line[:TD3_LINE_LENGTH], False, ""

    if len(line) < MIN_RECONSTRUCTABLE_LINE2:
        # Too short to have kept an intact prefix; rebuilding would invent data.
        return line, False, ""

    # Only filler may be missing. If the shortfall cannot be explained by
    # filler alone, the line lost real characters and reconstructing it would
    # silently shift every field -- far worse than reporting nothing.
    if FILLER not in line:
        return line, False, ""

    body, composite = line[:-1], line[-1]

    # Find the trailing filler run and grow it until the line reaches width.
    end = len(body)
    while end > 0 and body[end - 1] == FILLER:
        end -= 1

    if end < PROTECTED_PREFIX_END:
        # The filler run reaches back into protected positions, so we cannot
        # tell which characters were lost. Decline.
        return line, False, ""

    rebuilt = body[:end] + FILLER * (TD3_LINE_LENGTH - 1 - end) + composite
    note = (
        f"Line 2 was {len(line)} characters; the standard requires "
        f"{TD3_LINE_LENGTH}. The shortfall was in the filler run after "
        f"position {end}, so it was re-inflated. Positions 0-{PROTECTED_PREFIX_END - 1} "
        f"were untouched, so the document number, date of birth and expiry "
        f"check digits remain valid evidence; the personal-number and composite "
        f"digits do not and are excluded."
    )
    return rebuilt, True, note


def parse_td3(line1: str, line2: str) -> MRZData:
    """
    Parse a TD3 (passport) MRZ and verify all five check digits.

    Field offsets are fixed by ICAO 9303 and are the reason MRZ validation is
    reliable: every value sits at a known position, so a mismatch is a fact
    rather than an inference.
    """
    data = MRZData()

    # Judge line 1's filler content BEFORE padding. _pad_or_trim pads with the
    # filler character itself, so measuring afterwards counts padding we just
    # added and concludes the line was fine -- which is exactly what happened
    # the first time this check was written.
    raw_line1 = line1.upper().replace(" ", "")
    data.line1_unreliable = raw_line1.count(FILLER) < 3

    line1 = _pad_or_trim(raw_line1, TD3_LINE_LENGTH)

    # Line 2 gets reconstruction rather than blind padding: blind padding
    # would append filler at the END, leaving the composite check digit in the
    # wrong position and silently producing a failed composite check that
    # looks exactly like tampering.
    line2, reconstructed, note = reconstruct_line2(line2)
    data.line2_reconstructed = reconstructed
    data.reconstruction_note = note
    line2 = _pad_or_trim(line2, TD3_LINE_LENGTH)

    data.raw_line1, data.raw_line2 = line1, line2

    # --- Line 1: document code, issuing state, name ---
    #
    # Line 1 is entirely filler-delimited, which makes it the part OCR ruins
    # most completely. A genuine TD3 line 1 is mostly filler -- the name field
    # is 39 characters and almost never full. If the fillers are gone, every
    # offset below is meaningless: the issuing country reads three letters of
    # the surname, and the surname swallows the given names.
    #
    # Two independent signs of that, either of which is conclusive:
    #   * position 1 must be filler or a document sub-type, never a letter
    #     belonging to the country code;
    #   * a line 1 with almost no filler cannot be a real TD3 line 1.
    filler_count = raw_line1.count(FILLER)

    data.document_code = line1[0:2].rstrip(FILLER)
    data.issuing_country = line1[2:5].rstrip(FILLER)
    data.surname, data.given_names, data.name_full = parse_mrz_name(line1[5:44])

    if data.line1_unreliable:
        # Do not hand back values we know are wrong. An empty field is honest;
        # a confidently wrong surname is actively harmful downstream.
        data.issuing_country = ""
        data.surname = ""
        data.given_names = ""
        data.name_full = ""
        data.parse_errors.append(
            f"Line 1 contains only {filler_count} filler characters, so the "
            f"name and issuing-country fields could not be located. Text "
            f"recognition dropped the separators; the name was not recovered."
        )
    elif not data.document_code.startswith("P"):
        data.parse_errors.append(
            f"Document code is {data.document_code!r}, expected P for a passport"
        )

    # --- Line 2: fixed-offset data fields, each with its own check digit ---
    doc_number = line2[0:9]
    doc_number_cd = line2[9]
    nationality = line2[10:13]
    dob_raw = line2[13:19]
    dob_cd = line2[19]
    sex = line2[20]
    expiry_raw = line2[21:27]
    expiry_cd = line2[27]
    personal_number = line2[28:42]
    personal_cd = line2[42]
    composite_cd = line2[43]

    data.document_number = doc_number.rstrip(FILLER)
    data.nationality = nationality.rstrip(FILLER)
    data.sex = sex if sex in ("M", "F") else ""
    data.personal_number = personal_number.rstrip(FILLER)

    data.date_of_birth = parse_mrz_date(dob_raw)
    if data.date_of_birth is None:
        data.parse_errors.append(f"Date of birth {dob_raw!r} is not a valid date")

    data.date_of_expiry = parse_mrz_date(expiry_raw, is_expiry=True)
    if data.date_of_expiry is None:
        data.parse_errors.append(f"Expiry date {expiry_raw!r} is not a valid date")

    # The composite digit covers the data fields AND their individual check
    # digits, so editing any one value invalidates two digits at once. That
    # redundancy is what makes MRZ tampering hard to fake by hand.
    composite_data = line2[0:10] + line2[13:20] + line2[21:43]

    for name, value, stated in (
        ("document_number", doc_number, doc_number_cd),
        ("date_of_birth", dob_raw, dob_cd),
        ("date_of_expiry", expiry_raw, expiry_cd),
        ("personal_number", personal_number, personal_cd),
        ("composite", composite_data, composite_cd),
    ):
        try:
            computed = check_digit(value)
            valid = verify_check_digit(value, stated)
        except ValueError as exc:
            computed, valid = -1, False
            data.parse_errors.append(f"{name}: {exc}")

        data.checks.append(
            CheckResult(
                field_name=name,
                raw_value=value,
                stated_digit=stated,
                computed_digit=computed,
                valid=valid,
            )
        )

    return data


def parse_mrz(lines: list[str]) -> MRZData | None:
    """
    Parse MRZ from candidate OCR lines.

    Returns None when no line pair is structurally plausible, which the caller
    reports as "MRZ not found" -- deliberately distinct from "MRZ found and
    invalid". Confusing those two would turn a bad photograph into a fraud
    accusation.
    """
    # 30, not 44: real OCR drops MRZ filler characters, so insisting on full
    # width here rejected genuine passports outright -- measured at 2 MRZ found
    # across 14 real specimen documents. reconstruct_line2 handles the shortfall
    # and refuses when the loss cannot be attributed to filler.
    usable = [ln for ln in lines if len(ln) >= MIN_RECONSTRUCTABLE_LINE2]
    if len(usable) < TD3_LINE_COUNT:
        return None

    # Line 2 is identifiable by structure rather than position: it starts with
    # the 9-character document number and carries digits in the date fields,
    # whereas line 1 begins with the document code and is mostly letters.
    # Choosing by shape rather than "the last two lines" matters because OCR
    # does not reliably return the MRZ band last.
    def line2_score(s: str) -> float:
        digits = sum(c.isdigit() for c in s)
        return digits / max(len(s), 1)

    ranked = sorted(usable, key=line2_score, reverse=True)
    candidate_l2 = ranked[0]

    # Line 1 is the best remaining candidate that starts like a document code.
    rest = [ln for ln in usable if ln is not candidate_l2]
    candidate_l1 = next((ln for ln in rest if ln.startswith("P")), rest[-1] if rest else "")

    return parse_td3(candidate_l1, candidate_l2)
