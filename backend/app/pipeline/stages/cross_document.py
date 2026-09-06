"""
Cross-document intelligence (Layer 7).

Compares the fields recovered from several documents belonging to one case.
This is where a case becomes more than its parts: two individually flawless
documents that disagree about a date of birth are a finding neither produces
alone.

The hard problem here is not comparison, it is knowing when a difference is
actually a difference. Indian identity documents legitimately disagree in
ways that are not fraud:

  * Transliteration.  RAJDEEP / RAJDIP / RAJDEEPA are the same name rendered
    from Devanagari by three different clerks.
  * Name order.       Passports print SURNAME then GIVEN NAMES; a PAN card
    prints given name first.
  * Initials.         "R SUMAN" versus "RAJDEEP SUMAN".
  * Honorifics.       "SHRI", "SMT", "MR", "DR".
  * Placeholder DOBs. 01/01/YYYY is routinely recorded when only the birth
    YEAR is known, especially for older applicants.

Treating any of these as fraud would generate false accusations against
exactly the people least able to contest them. So name comparison is fuzzy and
order-insensitive, and a WARN is reserved for differences that survive all of
the above.
"""

from __future__ import annotations

from datetime import date
from itertools import combinations

from rapidfuzz import fuzz

from app.schemas.document import DocumentAnalysis
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

# Token-set similarity above which two names are treated as the same person.
# Chosen to absorb transliteration and spelling variation while still
# separating genuinely different names.
NAME_MATCH_THRESHOLD = 85.0
NAME_WEAK_THRESHOLD = 70.0

_HONORIFICS = {
    "MR", "MRS", "MS", "MISS", "DR", "PROF",
    "SHRI", "SHRIMATI", "SMT", "SRI", "KUMARI", "KM",
}


def normalize_name(name: str) -> str:
    """
    Reduce a name to a comparable form.

    Uppercases, strips honorifics and punctuation, and collapses whitespace.
    Word ORDER is deliberately preserved here -- the order-insensitivity comes
    from using token_set_ratio at comparison time, which keeps this function
    honest about what it changed.
    """
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name.upper())
    tokens = [t for t in cleaned.split() if t and t not in _HONORIFICS]
    return " ".join(tokens)


def names_match(a: str, b: str) -> tuple[bool, float]:
    """
    Compare two names, tolerant of order, initials and transliteration.

    token_set_ratio handles reordering and subset cases ("R SUMAN" inside
    "RAJDEEP SUMAN") which is precisely the variation real documents show.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False, 0.0
    score = max(
        fuzz.token_set_ratio(na, nb),
        fuzz.partial_ratio(na, nb) * 0.95,  # slightly discounted: weaker evidence
    )
    return score >= NAME_MATCH_THRESHOLD, float(score)


def _is_placeholder_dob(d: date) -> bool:
    """1 January is the conventional placeholder when only the year is known."""
    return d.month == 1 and d.day == 1


def _doc_label(doc: DocumentAnalysis) -> str:
    return doc.document_type.value.replace("_", " ") or doc.filename


def compare_names(docs: list[DocumentAnalysis]) -> list[Signal]:
    """Pairwise name comparison across all documents in the case."""
    named = [
        (d, str(d.fields.full_name.value))
        for d in docs
        if d.fields.full_name.present
    ]
    if len(named) < 2:
        return []

    signals: list[Signal] = []
    for (doc_a, name_a), (doc_b, name_b) in combinations(named, 2):
        matched, score = names_match(name_a, name_b)
        label_a, label_b = _doc_label(doc_a), _doc_label(doc_b)

        if matched:
            signals.append(
                signal(
                    code="cross.name.match",
                    stage=Stage.CROSS_DOC,
                    title="Name consistency",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    confidence=score / 100,
                    reason=(
                        f"The name on the {label_a} ({name_a}) and the {label_b} "
                        f"({name_b}) agree ({score:.0f}% similarity)."
                    ),
                    evidence={"a": name_a, "b": name_b, "similarity": round(score, 1)},
                )
            )
        elif score >= NAME_WEAK_THRESHOLD:
            signals.append(
                signal(
                    code="cross.name.weak",
                    stage=Stage.CROSS_DOC,
                    title="Name consistency",
                    status=SignalStatus.WARN,
                    severity=Severity.LOW,
                    confidence=0.6,
                    reason=(
                        f"The name on the {label_a} ({name_a}) and the {label_b} "
                        f"({name_b}) are similar but not equal ({score:.0f}%). "
                        f"Transliteration from an Indian-language script routinely "
                        f"produces this, so it is usually benign -- but worth a look."
                    ),
                    evidence={"a": name_a, "b": name_b, "similarity": round(score, 1)},
                )
            )
        else:
            signals.append(
                signal(
                    code="cross.name.mismatch",
                    stage=Stage.CROSS_DOC,
                    title="Name consistency",
                    status=SignalStatus.FAIL,
                    severity=Severity.HIGH,
                    confidence=0.85,
                    reason=(
                        f"The name on the {label_a} ({name_a}) does not match the "
                        f"{label_b} ({name_b}) -- only {score:.0f}% similar, well "
                        f"below what spelling or transliteration variation explains. "
                        f"These documents may not describe the same person."
                    ),
                    evidence={"a": name_a, "b": name_b, "similarity": round(score, 1)},
                )
            )

    return signals


def compare_dates_of_birth(docs: list[DocumentAnalysis]) -> list[Signal]:
    """Pairwise date-of-birth comparison."""
    dated = [
        (d, d.fields.date_of_birth.value)
        for d in docs
        if d.fields.date_of_birth.present
        and isinstance(d.fields.date_of_birth.value, date)
    ]
    if len(dated) < 2:
        return []

    signals: list[Signal] = []
    for (doc_a, dob_a), (doc_b, dob_b) in combinations(dated, 2):
        label_a, label_b = _doc_label(doc_a), _doc_label(doc_b)

        if dob_a == dob_b:
            signals.append(
                signal(
                    code="cross.dob.match",
                    stage=Stage.CROSS_DOC,
                    title="Date of birth consistency",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason=(
                        f"Date of birth agrees across the {label_a} and {label_b} "
                        f"({dob_a.isoformat()})."
                    ),
                    evidence={"dob": dob_a.isoformat()},
                )
            )
            continue

        # Same year, one side a 1 January placeholder: an administrative
        # convention, not a contradiction.
        placeholder = (
            dob_a.year == dob_b.year
            and (_is_placeholder_dob(dob_a) or _is_placeholder_dob(dob_b))
        )
        if placeholder:
            signals.append(
                signal(
                    code="cross.dob.placeholder",
                    stage=Stage.CROSS_DOC,
                    title="Date of birth consistency",
                    status=SignalStatus.WARN,
                    severity=Severity.LOW,
                    confidence=0.7,
                    reason=(
                        f"The {label_a} gives {dob_a.isoformat()} and the {label_b} "
                        f"gives {dob_b.isoformat()}. The birth YEAR agrees and one "
                        f"value is 1 January, which is the standard placeholder when "
                        f"only the year of birth was known at issue. Probably benign."
                    ),
                    evidence={"a": dob_a.isoformat(), "b": dob_b.isoformat()},
                )
            )
            continue

        delta_days = abs((dob_a - dob_b).days)
        # A transposed day/month (12/04 vs 04/12) is a common clerical error
        # and looks different from an invented date.
        transposed = dob_a.day == dob_b.month and dob_a.month == dob_b.day

        signals.append(
            signal(
                code="cross.dob.mismatch",
                stage=Stage.CROSS_DOC,
                title="Date of birth consistency",
                status=SignalStatus.FAIL,
                severity=Severity.CRITICAL if delta_days > 365 else Severity.HIGH,
                confidence=0.9,
                reason=(
                    f"Date of birth does not match: the {label_a} states "
                    f"{dob_a.isoformat()} while the {label_b} states "
                    f"{dob_b.isoformat()} -- a difference of {delta_days} days."
                    + (
                        " The day and month appear transposed, which is a common "
                        "data-entry error rather than a sign of fraud."
                        if transposed
                        else " A person has one date of birth; one of these "
                        "documents is wrong."
                    )
                ),
                evidence={
                    "a": dob_a.isoformat(),
                    "b": dob_b.isoformat(),
                    "delta_days": delta_days,
                    "day_month_transposed": transposed,
                },
            )
        )

    return signals


def compare_documents(docs: list[DocumentAnalysis]) -> list[Signal]:
    """
    Run every cross-document check.

    With fewer than two documents there is nothing to compare, and we say so
    explicitly -- a case assessed on one document should not appear to have
    passed a consistency check it never ran.
    """
    if len(docs) < 2:
        return [
            signal(
                code="cross.not_applicable",
                stage=Stage.CROSS_DOC,
                title="Cross-document consistency",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "Only one document was submitted, so no cross-document "
                    "consistency checks were possible. Submitting a second "
                    "document would materially strengthen this assessment."
                ),
            )
        ]

    signals: list[Signal] = []
    signals.extend(compare_names(docs))
    signals.extend(compare_dates_of_birth(docs))

    if not signals:
        signals.append(
            signal(
                code="cross.no_comparable_fields",
                stage=Stage.CROSS_DOC,
                title="Cross-document consistency",
                status=SignalStatus.SKIP,
                severity=Severity.MEDIUM,
                reason=(
                    f"{len(docs)} documents were submitted, but no field was "
                    f"successfully extracted from two or more of them, so nothing "
                    f"could be compared. This is a gap in the evidence, not a pass."
                ),
            )
        )

    return signals
