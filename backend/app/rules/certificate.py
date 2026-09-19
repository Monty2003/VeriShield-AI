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

Not every certificate is a marksheet. Course-completion, internship,
participation, training and award certificates have no marks table, no board
and nothing that repeats itself -- measured, 8 of 12 certificates collected for
this project were of that kind. Nothing on them can be checked offline, so
they are told apart and sent to a person with what to check, rather than run
through marksheet rules that happen to find the word "University" somewhere
and pass them.
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

# Wording of course, participation, internship, training and award
# certificates. Shared with the classifier, which uses it as type evidence.
CREDENTIAL_WORDING = re.compile(
    r"THIS\s+IS\s+TO\s+CERTIFY|CERTIF(?:Y|IES|IED)\s+THAT"
    r"|SUCCESSFULLY\s+(?:COMPLETED|PARTICIPATED)|HAS\s+(?:PARTICIPATED|COMPLETED|BEEN\s+AWARDED)"
    r"|FOR\s+PARTICIPAT|IN\s+RECOGNITION\s+OF|AWARDED\s+TO|PRESENTED\s+TO"
    r"|OF\s+(?:COMPLETION|PARTICIPATION|ACHIEVEMENT|APPRECIATION|EXCELLENCE|MERIT)"
    r"|\bINTERNSHIP\b|CONGRATULAT",
    re.I,
)

# A marks table. Its presence makes a document a marksheet whatever else it says:
# a CBSE statement is also "awarded" and "certified", and the table is what
# carries the checkable redundancy.
_MARKS_TABLE = re.compile(
    r"MARKS\s*OBTAINED|\bTHEORY\b|\bPRACTICAL\b|\bSEMESTER\b|\bS?CGPA\b|\bSGPA\b"
    r"|GRADE\s*POINT|ROLL\s*N[O0]|MARK\s*-?\s*SHEET|STATEMENT\s*OF\s*MARKS"
    r"|TOTAL\s*MARKS|MAX(?:IMUM)?\s*MARKS",
    re.I,
)

# Where a certificate says it can be checked: an ID or a web address.
#
# Certificates label their IDs every way there is. Only "certificate ID" was
# recognised, and a genuine internship certificate printing a "Student ID"
# was reported as printing no ID at all.
_PRINTED_ID = re.compile(
    r"\b(?:CERTIFICATE|CREDENTIAL|CERT|STUDENT|REGISTRATION|REGN?|ENROL+MENT|SERIAL"
    r"|VERIFICATION|REFERENCE|REF|LICEN[CS]E|MEMBERSHIP|UNIQUE)"
    r"\.?\s*(?:ID|NO|NUMBER|CODE|#)\b\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-.]{3,})",
    re.I,
)
_WEB_ADDRESS = re.compile(r"https?://[^\s]+|\bwww\.[^\s]+", re.I)

# --- dates -------------------------------------------------------------------
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH = r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?"
# No word boundary before the day: OCR joins it to the word before
# ("2024 to30th December 2024"), and a boundary would lose the date.
_DATE_PATTERNS = (
    ("dmy", re.compile(r"(?<!\d)(\d{1,2})(?:ST|ND|RD|TH)?\s*(?:OF\s+)?" + _MONTH + r",?\s*(\d{4})\b", re.I)),
    ("mdy", re.compile(r"\b" + _MONTH + r"\s+(\d{1,2})(?:ST|ND|RD|TH)?,?\s*(\d{4})\b", re.I)),
    # Numeric dates are read day first, as Indian documents print them.
    ("num", re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")),
)
_PERIOD_JOIN = re.compile(r"^\s*(?:TO|TILL|UNTIL|-|\u2013|\u2014)\s*$", re.I)
_ISSUE_LABEL = re.compile(r"DATE\s*OF\s*ISSUE|ISSUED?\s*(?:ON|DATE)|ISSUE\s*DATE|\bDATED\b", re.I)
_VALIDITY_LABEL = re.compile(r"VALID|EXPIR|UP\s*TO|UPTO|RENEW", re.I)

_ROLL_NO = re.compile(r"ROLL\s*N[O0]\.?\s*:?\s*([0-9]{6,12})", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def words_to_number(phrase: str) -> int | None:
    """
    Parse an English number phrase into an integer.

    Handles the forms a marksheet actually prints -- "SIXTY THREE", "ONE
    HUNDRED", "NINETY" -- and returns None for anything it cannot parse rather
    than guessing, since a wrong reading here would manufacture a mismatch.
    """
    tokens: list[str] = []
    for chunk in re.split(r"[\s-]+", phrase.upper()):
        joined = _split_joined(chunk)
        # A chunk made wholly of number words, including OCR's joined form
        # ("SIXTYSEVEN"); anything else is read the old way, word by word.
        tokens.extend(joined if joined is not None else _NUMBER_WORD.findall(chunk))
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


_WORD_ALTERNATION = "|".join(
    sorted(set(_UNITS) | set(_TENS) | set(_SCALES), key=len, reverse=True)
)
# Separators may be absent: OCR joins the words of a narrow cell. Measured on a
# genuine marksheet, 3 of 5 totals came out joined ("SIXTYSEVEN" for "SIXTY
# SEVEN"), and none of those rows was being checked at all. The outer
# word boundaries still keep this out of ordinary words ("TENANT", "OFTEN").
_NUMBER_PHRASE = re.compile(
    r"\b((?:" + _WORD_ALTERNATION + r")(?:[\s-]*(?:" + _WORD_ALTERNATION + r"))*)\b"
)
_NUMBER_TOKEN = re.compile(_WORD_ALTERNATION)


def _split_joined(chunk: str) -> list[str] | None:
    """"SIXTYSEVEN" -> ["SIXTY", "SEVEN"]; None unless the chunk is number words only."""
    tokens: list[str] = []
    at = 0
    while at < len(chunk):
        match = _NUMBER_TOKEN.match(chunk, at)
        if match is None:
            return None
        tokens.append(match.group(0))
        at = match.end()
    return tokens or None


def _number_word_matches(text: str) -> list[tuple[str, int, int]]:
    """Spelled-out numbers as (phrase, value, position in the upper-cased text)."""
    results: list[tuple[str, int, int]] = []
    for match in _NUMBER_PHRASE.finditer(text.upper()):
        value = words_to_number(match.group(1))
        if value is not None:
            results.append((match.group(1), value, match.start(1)))
    return results


def find_number_word_phrases(text: str) -> list[tuple[str, int]]:
    """
    Find spelled-out numbers and their values.

    Consecutive number words are grouped, so "SIXTY THREE" yields 63 rather
    than 60 and 3 separately.
    """
    return [(phrase, value) for phrase, value, _ in _number_word_matches(text)]


def printed_ids(text: str) -> list[str]:
    """IDs the certificate prints under a label; a value must contain a digit."""
    return [
        match.group(1).rstrip(".-/")
        for match in _PRINTED_ID.finditer(text)
        if any(ch.isdigit() for ch in match.group(1))
    ]


def _dates_with_positions(flat: str) -> list[tuple[int, int, date]]:
    """(start, end, date) for every readable date in whitespace-normalised text."""
    found: list[tuple[int, int, date]] = []
    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(flat):
            try:
                if kind == "dmy":
                    day, month, year = int(match.group(1)), _MONTHS[match.group(2).upper()[:3]], int(match.group(3))
                elif kind == "mdy":
                    month, day, year = _MONTHS[match.group(1).upper()[:3]], int(match.group(2)), int(match.group(3))
                else:
                    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                found.append((match.start(), match.end(), date(year, month, day)))
            except (ValueError, KeyError):
                continue  # 31 February, or a table figure that only looks like a date
    # One date per place in the text, first pattern winning.
    found.sort()
    kept: list[tuple[int, int, date]] = []
    for start, end, when in found:
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, end, when))
    return kept


def _date_signals(text: str, today: date | None = None) -> list[Signal]:
    """
    The dates a course or internship certificate states, checked against each
    other and against today: a period that ends before it starts, a completion
    certificate issued before the programme ended, a date not yet reached.
    """
    today = today or date.today()
    flat = re.sub(r"\s+", " ", text)
    dates = _dates_with_positions(flat)
    if not dates:
        return []

    def label_near(start: int, end: int, pattern: re.Pattern[str], reach: int = 40) -> bool:
        return bool(pattern.search(flat[max(0, start - reach) : end + reach]))

    periods = [
        (a[2], b[2])
        for a, b in zip(dates, dates[1:])
        if _PERIOD_JOIN.match(flat[a[1] : b[0]])
    ]
    # The issue date is the date NEAREST an issue label, on either side --
    # "Date of Issue" is printed under its date as often as before it, and
    # the first date within reach was sometimes the period's start instead.
    labels = [(m.start(), m.end()) for m in _ISSUE_LABEL.finditer(flat)]
    nearest = sorted(
        (min(max(l_start - end, start - l_end, 0) for l_start, l_end in labels), when)
        for start, end, when in dates
        if labels
    )
    issued = nearest[0][1] if nearest and nearest[0][0] <= 40 else None
    future = [
        when
        for start, end, when in dates
        if when > today and not label_near(start, end, _VALIDITY_LABEL)
    ]

    problems: list[str] = []
    if future:
        problems.append(
            f"it is dated {future[0]:%d %b %Y}, which has not happened yet"
        )
    for begins, ends in periods:
        if ends < begins:
            problems.append(
                f"its period ends ({ends:%d %b %Y}) before it begins ({begins:%d %b %Y})"
            )
    if issued is not None:
        for _, ends in periods:
            if issued < ends:
                problems.append(
                    f"it was issued on {issued:%d %b %Y}, before the period it "
                    f"certifies ended on {ends:%d %b %Y}"
                )

    if problems:
        return [
            signal(
                code="certificate.dates.inconsistent",
                stage=Stage.VALIDATE,
                title="Dates on the certificate",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                # OCR can misread a digit in a date, so this escalates rather
                # than rejects -- but a genuine certificate does not do this.
                confidence=0.7,
                blocking=True,
                reason=(
                    "The certificate's dates do not fit together: "
                    + "; ".join(problems)
                    + ". A genuine certificate is issued after what it certifies, "
                    "and on a date that has already come. Check the original."
                ),
                evidence={"dates": [d.isoformat() for _, _, d in dates][:6]},
            )
        ]
    if periods or issued is not None:
        stated = []
        if periods:
            stated.append(f"a period of {periods[0][0]:%d %b %Y} to {periods[0][1]:%d %b %Y}")
        if issued is not None:
            stated.append(f"issue on {issued:%d %b %Y}")
        return [
            signal(
                code="certificate.dates.consistent",
                stage=Stage.VALIDATE,
                title="Dates on the certificate",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                confidence=0.7,
                reason=(
                    f"The certificate states {' and '.join(stated)}, in a possible "
                    f"order and none in the future. That rules out a careless "
                    f"edit of a date; it does not confirm the certificate."
                ),
                evidence={"dates": [d.isoformat() for _, _, d in dates][:6]},
            )
        ]
    return []


def certificate_kind(text: str) -> str:
    """
    "marksheet" or "credential".

    A credential is a course, participation, internship, training or award
    certificate: its wording says so and it has no marks table. Anything else
    stays a marksheet, which is what this rulebook was built for.
    """
    if CREDENTIAL_WORDING.search(text) and not _MARKS_TABLE.search(text):
        return "credential"
    return "marksheet"


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

    if certificate_kind(text) == "credential":
        return _validate_credential(text)

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
                reason=(
                    f"A roll number (ending {roll.group(1)[-4:]}) was read from "
                    f"the certificate."
                ),
                evidence={"roll_number": roll.group(1)},
            )
        )

    signals.extend(_year_signals(text))

    # --- the real check: totals stated in digits AND in words ---
    signals.extend(_check_totals_against_words(text))

    return signals


def _year_signals(text: str) -> list[Signal]:
    signals: list[Signal] = []
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
    return signals


def _validate_credential(text: str) -> list[Signal]:
    """
    A course, participation, internship, training or award certificate.

    There is no offline check for one: no checksum, no signature this system
    can verify, no public register it can consult. Saying so -- and pointing at
    whatever the certificate offers for checking it -- is the honest result.
    A pass here would be a pass for having found nothing wrong in a document
    nothing could be checked on.
    """
    signals: list[Signal] = [
        signal(
            code="certificate.kind.credential",
            stage=Stage.VALIDATE,
            title="Certificate kind",
            status=SignalStatus.SKIP,
            severity=Severity.INFO,
            reason=(
                "This is a course, participation, internship, training or award "
                "certificate rather than a marksheet, so the marksheet checks -- "
                "issuing board, and totals in digits against words -- do not apply."
            ),
        )
    ]

    ids = printed_ids(text)
    has_id = bool(ids)
    domains = sorted(
        {
            re.sub(r"^(?:https?://)?(?:www\.)?", "", address, flags=re.I).split("/")[0].lower()
            for address in _WEB_ADDRESS.findall(text)
        }
        - {""}
    )
    if has_id or domains:
        where = []
        if has_id:
            where.append(f"an ID (ending {ids[0][-4:]})")
        if domains:
            where.append("a web address (" + ", ".join(domains[:3]) + ")")
        signals.append(
            signal(
                code="certificate.reference.found",
                stage=Stage.VALIDATE,
                title="How to confirm it",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"The certificate prints {' and '.join(where)}. That is how the "
                    f"issuer lets it be confirmed: look it up on the issuer's own "
                    f"site, which should show the same name and details."
                ),
                evidence={"certificate_id_printed": has_id, "domains": domains[:5]},
            )
        )

    signals.extend(_year_signals(text))
    signals.extend(_date_signals(text))

    signals.append(
        signal(
            code="certificate.credential.unverifiable",
            stage=Stage.VALIDATE,
            title="Offline verification",
            # WARN and LOW: nothing here suggests forgery. Blocking: nothing
            # here establishes authenticity either, and acceptance must rest
            # on something that was actually checked.
            status=SignalStatus.WARN,
            severity=Severity.LOW,
            blocking=True,
            reason=(
                "Nothing on a certificate of this kind can be verified offline: it "
                "carries no checksum, no signature this system can check, and no "
                "public register it can consult. Confirm it with the issuer -- "
                + (
                    "through the certificate ID or web address it prints, or the "
                    "link in its QR code."
                    if has_id or domains
                    else "it prints no certificate ID or web address, so contact "
                    "the issuing organisation directly."
                )
            ),
        )
    )
    return signals


def _totals_unchecked(code: str, what: str) -> Signal:
    """
    The marksheet's one offline check did not run.

    Blocking, like every other check that could not establish authenticity:
    a board's name is all that is left, and anyone can print a board's name.
    This was a SKIP, and a marksheet's reverse -- instructions, no marks --
    was auto-accepted on the strength of naming CBSE.
    """
    return signal(
        code=code,
        stage=Stage.VALIDATE,
        title="Marks in digits vs words",
        status=SignalStatus.WARN,
        severity=Severity.LOW,
        blocking=True,
        reason=(
            f"{what}, so this marksheet's one internal cross-check -- each total "
            f"in digits against the same total in words -- could not be applied, "
            f"and nothing else on it can be verified offline. Photograph the marks "
            f"table again so both columns are legible, or have a person check it. "
            f"If this is the reverse of the marksheet, submit the front."
        ),
    )


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
    # Each phrase with its OWN position. Locating it by searching for its text
    # found the first occurrence instead: on a genuine marksheet whose total
    # "SEVENTY" came after a row reading "SEVENTY THREE", 70 was compared with
    # the 73 of that earlier row and reported as altered.
    matches = _number_word_matches(text)
    # Ignore bare "ONE"/"TWO" and similar: they appear in ordinary prose
    # ("one of", "two years") far more often than as a stated total.
    matches = [(p, v, at) for p, v, at in matches if " " in p or v >= 20]
    phrases = [(p, v) for p, v, _ in matches]

    if not phrases:
        return [_totals_unchecked(
            "certificate.totals.no_words",
            "No totals written out in words were found",
        )]

    upper = text.upper()
    matched: list[dict] = []
    mismatched: list[dict] = []

    for phrase, value, position in matches:
        # Numeric tokens printed just before the words, which is where the
        # table places the figure this phrase spells out.
        preceding = re.findall(r"\b(\d{1,3})\b", upper[max(0, position - 60) : position])
        candidates = [int(n) for n in preceding]
        # OCR does not always keep the table's reading order: on a genuine
        # marksheet the words came out first and their figure on the next line.
        # Only the single figure immediately after is accepted, so a changed
        # mark cannot find its old value somewhere further down the table.
        end = position + len(phrase)
        following = re.search(r"\b(\d{1,3})\b", upper[end : end + 25])

        if value in candidates or (following and int(following.group(1)) == value):
            matched.append({"words": phrase, "value": value})
        elif not candidates:
            continue
        else:
            mismatched.append(
                {"words": phrase, "words_value": value, "digits_nearby": candidates[-4:]}
            )

    signals: list[Signal] = []

    if not matched and not mismatched:
        signals.append(_totals_unchecked(
            "certificate.totals.unchecked",
            "Totals written in words were found, but no figures could be paired with them",
        ))

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
                # a guarantee. Strong enough to escalate, not to conclude --
                # so blocking. It was not, and a marksheet whose digits and
                # words disagreed was auto-accepted on a score of 15.
                blocking=True,
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
