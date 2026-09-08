"""
Academic certificate and marksheet validation rules (Layer 4).

A marksheet has no checksum, but a CBSE-style statement carries something
almost as good: it states each subject's total TWICE, once in digits and once
in words. It also states the components -- theory and internal assessment --
that must sum to that total.

That redundancy is the whole basis of this rulebook. Raising a mark means
editing the digits, the words, and the components, all consistently. Forgers
routinely change the digits and stop, because the digits are what the eye goes
to. The words are what catches them.

This is the same principle as an MRZ check digit, arrived at differently: the
document repeats information about itself, so an inconsistent copy is provably
altered without consulting any issuing authority.
"""

from __future__ import annotations

import re
from datetime import date

from app.schemas.document import ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

# --- number words ----------------------------------------------------------

_UNITS = {
    "ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
    "ELEVEN": 11, "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14,
    "FIFTEEN": 15, "SIXTEEN": 16, "SEVENTEEN": 17, "EIGHTEEN": 18,
    "NINETEEN": 19,
}
_TENS = {
    "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FOURTY": 40,  # 'fourty' is a common print error
    "FIFTY": 50, "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90,
}
_SCALES = {"HUNDRED": 100}

_NUMBER_WORD = re.compile(
    r"\b(" + "|".join(sorted(set(_UNITS) | set(_TENS) | set(_SCALES), key=len, reverse=True)) + r")\b"
)

# Marks are recorded out of 100 per subject in this format. A value outside
# that range is either a misread or an impossible mark.
MAX_SUBJECT_MARK = 100

# Examinations before this are not plausible on a document presented today, and
# a year in the future cannot have been examined yet.
EARLIEST_PLAUSIBLE_EXAM_YEAR = 1950

_BOARDS = (
    (re.compile(r"CENTRAL\s*BOARD\s*OF\s*SECONDARY\s*EDUCATION", re.I), "CBSE"),
    (re.compile(r"\bCBSE\b", re.I), "CBSE"),
    (re.compile(r"COUNCIL\s*FOR\s*THE\s*INDIAN\s*SCHOOL", re.I), "CISCE"),
    (re.compile(r"\bICSE\b|\bISC\b", re.I), "CISCE"),
    (re.compile(r"NATIONAL\s*INSTITUTE\s*OF\s*OPEN\s*SCHOOLING", re.I), "NIOS"),
    (re.compile(r"BOARD\s*OF\s*SCHOOL\s*EXAMINATION", re.I), "State board"),
    (re.compile(r"\bUNIVERSITY\b", re.I), "University"),
)

_ROLL_NO = re.compile(r"ROLL\s*N[O0]\.?\s*:?\s*([0-9]{6,12})", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def words_to_number(phrase: str) -> int | None:
    """
    Parse an English number phrase into an integer.

    Handles the forms a marksheet actually prints -- "SIXTY THREE", "ONE
    HUNDRED", "NINETY" -- and returns None for anything it cannot parse rather
    than guessing, since a wrong reading here would manufacture a mismatch.
    """
    tokens = _NUMBER_WORD.findall(phrase.upper())
    if not tokens:
        return None

    total = 0
    current = 0
    for token in tokens:
        if token in _UNITS:
            current += _UNITS[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token in _SCALES:
            current = max(current, 1) * _SCALES[token]
            total += current
            current = 0
    return total + current


def find_number_word_phrases(text: str) -> list[tuple[str, int]]:
    """
    Find spelled-out numbers and their values.

    Consecutive number words are grouped, so "SIXTY THREE" yields 63 rather
    than 60 and 3 separately.
    """
    upper = text.upper()
    results: list[tuple[str, int]] = []

    for match in re.finditer(
        r"\b((?:" + "|".join(sorted(set(_UNITS) | set(_TENS) | set(_SCALES), key=len, reverse=True))
        + r")(?:[\s-]+(?:" + "|".join(sorted(set(_UNITS) | set(_TENS) | set(_SCALES), key=len, reverse=True))
        + r"))*)\b",
        upper,
    ):
        phrase = match.group(1)
        value = words_to_number(phrase)
        if value is not None:
            results.append((phrase, value))
    return results


def validate_certificate(_mrz=None, fields: ExtractedFields | None = None) -> list[Signal]:
    """
    Run the certificate rulebook.

    Works from the raw text captured during extraction, because a marksheet's
    verifiable content is its table rather than a fixed set of identity fields.
    """
    text = ""
    if fields is not None and fields.raw_text.present:
        text = str(fields.raw_text.value)

    if not text.strip():
        return [
            signal(
                code="certificate.no_text",
                stage=Stage.VALIDATE,
                title="Certificate content",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    "No text was available from this certificate, so none of its "
                    "content could be validated."
                ),
            )
        ]

    signals: list[Signal] = []

    # --- issuing board ---
    board = next((name for pattern, name in _BOARDS if pattern.search(text)), None)
    if board:
        signals.append(
            signal(
                code="certificate.issuer.identified",
                stage=Stage.VALIDATE,
                title="Issuing authority",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=f"The certificate names a recognised issuing authority: {board}.",
                evidence={"issuer": board},
            )
        )
    else:
        signals.append(
            signal(
                code="certificate.issuer.unknown",
                stage=Stage.VALIDATE,
                title="Issuing authority",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                # Blocking: a credential whose issuer we cannot even name has
                # not been verified in any meaningful sense.
                blocking=True,
                reason=(
                    "No recognised examination board or university was found on "
                    "this certificate. Without knowing the issuer, its content "
                    "cannot be validated against any rules and it must be checked "
                    "by hand."
                ),
            )
        )

    # --- roll number ---
    roll = _ROLL_NO.search(text)
    if roll:
        signals.append(
            signal(
                code="certificate.roll_number.found",
                stage=Stage.VALIDATE,
                title="Roll number",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=f"Roll number {roll.group(1)} was read from the certificate.",
                evidence={"roll_number": roll.group(1)},
            )
        )

    # --- examination year ---
    years = sorted({int(y) for y in _YEAR.findall(text)})
    this_year = date.today().year
    if years:
        latest = max(years)
        if latest > this_year:
            signals.append(
                signal(
                    code="certificate.year.future",
                    stage=Stage.VALIDATE,
                    title="Examination year",
                    status=SignalStatus.FAIL,
                    severity=Severity.HIGH,
                    confidence=0.75,
                    reason=(
                        f"The certificate carries the year {latest}, which is in "
                        f"the future. An examination that has not been held cannot "
                        f"have produced a result."
                    ),
                    evidence={"years_found": years},
                )
            )
        else:
            signals.append(
                signal(
                    code="certificate.year.plausible",
                    stage=Stage.VALIDATE,
                    title="Examination year",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason=f"Examination year {latest} is plausible.",
                    evidence={"years_found": years},
                )
            )

    # --- the real check: totals stated in digits AND in words ---
    signals.extend(_check_totals_against_words(text))

    return signals


def _check_totals_against_words(text: str) -> list[Signal]:
    """
    Cross-check every spelled-out total against the digits printed beside it.

    This is the strongest offline check a marksheet supports. A forger raising
    a mark edits the digits, because that is what a reader looks at; the words
    are left behind, and the two then disagree.

    Matching is positional -- a spelled number is compared against the numeric
    tokens immediately before it -- because that is how the table is laid out,
    and comparing against the whole document would find a coincidental match
    for almost any value.
    """
    phrases = find_number_word_phrases(text)
    # Ignore bare "ONE"/"TWO" and similar: they appear in ordinary prose
    # ("one of", "two years") far more often than as a stated total.
    phrases = [(p, v) for p, v in phrases if " " in p or v >= 20]

    if not phrases:
        return [
            signal(
                code="certificate.totals.no_words",
                stage=Stage.VALIDATE,
                title="Marks stated in words",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "No totals written out in words were found, so the strongest "
                    "internal cross-check this document type supports could not "
                    "be applied. On a marksheet that prints totals both ways, a "
                    "clearer image would allow it."
                ),
            )
        ]

    upper = text.upper()
    matched: list[dict] = []
    mismatched: list[dict] = []

    for phrase, value in phrases:
        position = upper.find(phrase)
        # Numeric tokens printed just before the words, which is where the
        # table places the figure this phrase spells out.
        preceding = re.findall(r"\b(\d{1,3})\b", upper[max(0, position - 60) : position])
        candidates = [int(n) for n in preceding]

        if not candidates:
            continue
        if value in candidates:
            matched.append({"words": phrase, "value": value})
        else:
            mismatched.append(
                {"words": phrase, "words_value": value, "digits_nearby": candidates[-4:]}
            )

    signals: list[Signal] = []

    if mismatched:
        detail = "; ".join(
            f"{m['words']} ({m['words_value']}) printed beside {m['digits_nearby']}"
            for m in mismatched[:3]
        )
        signals.append(
            signal(
                code="certificate.totals.mismatch",
                stage=Stage.VALIDATE,
                title="Marks in digits vs words",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                # Real but not certain: OCR misreads digits on dense tables, and
                # the positional pairing is a heuristic about layout rather than
                # a guarantee. Strong enough to escalate, not to conclude.
                confidence=0.6,
                reason=(
                    f"{len(mismatched)} total(s) written in words do not match the "
                    f"figures printed alongside them: {detail}. A marksheet states "
                    f"each total twice precisely so the two can be compared, and "
                    f"altering a mark usually changes only the digits. Confirm "
                    f"against the original before acting -- dense tables are also "
                    f"where text recognition makes most of its mistakes."
                ),
                evidence={"mismatches": mismatched[:6], "matches": len(matched)},
            )
        )

    if matched:
        signals.append(
            signal(
                code="certificate.totals.match",
                stage=Stage.VALIDATE,
                title="Marks in digits vs words",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                confidence=0.8,
                reason=(
                    f"{len(matched)} total(s) written in words agree with the "
                    f"figures printed alongside them "
                    f"(e.g. {matched[0]['words']} = {matched[0]['value']}). "
                    f"Altering a mark requires changing both, consistently."
                ),
                evidence={"verified": matched[:8]},
            )
        )

    # --- impossible marks ---
    out_of_range = [v for _, v in phrases if v > MAX_SUBJECT_MARK]
    if out_of_range:
        signals.append(
            signal(
                code="certificate.marks.out_of_range",
                stage=Stage.VALIDATE,
                title="Marks range",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                confidence=0.5,
                reason=(
                    f"Value(s) above {MAX_SUBJECT_MARK} were written out as marks: "
                    f"{out_of_range[:4]}. Subject marks are recorded out of "
                    f"{MAX_SUBJECT_MARK}, so these are either aggregate totals "
                    f"read as subject marks, or impossible values."
                ),
                evidence={"values": out_of_range[:8]},
            )
        )

    return signals
