"""
Passport validation rules (Layer 4).

Turns parsed MRZ data plus visually-read fields into Signals. Every check
states what it compared and what it found, because the reviewer downstream
needs to act on the reason, not the score.

The strongest check here is not any single checksum -- it is the MRZ-versus-
visual-zone comparison. A forger editing a passport must alter the printed
field, the MRZ, and recompute two check digits consistently. Most do not.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.rules.mrz import MRZData
from app.schemas.document import ExtractedFields
from app.schemas.signals import (
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)

# Adult Indian passports are issued for 10 years, minors for 5. We allow a
# generous window and only flag values well outside it, since issuance rules
# differ by country and we must not punish legitimate variation.
MAX_PLAUSIBLE_VALIDITY_YEARS = 11
MAX_PLAUSIBLE_AGE_YEARS = 120

# How soon before expiry to raise an advisory. Many countries refuse entry on
# passports with under six months of validity, so this is materially useful
# to a reviewer even though it is not a fraud indicator.
EXPIRY_ADVISORY_DAYS = 180

# Which MRZ check digit failures are disqualifying on their own.
CRITICAL_CHECKS = {"composite", "document_number"}

_CHECK_LABELS = {
    "document_number": "passport number",
    "date_of_birth": "date of birth",
    "date_of_expiry": "expiry date",
    "personal_number": "personal number",
    "composite": "composite (whole MRZ)",
}


def validate_mrz_checks(mrz: MRZData) -> list[Signal]:
    """
    Emit one Signal per MRZ check digit.

    Checks excluded by `MRZData.trustworthy_checks` are reported as SKIP, not
    PASS. When line 2 had to be rebuilt from a short OCR reading, the
    personal-number and composite sums verify against filler this code
    inserted rather than against anything the document said -- they would pass
    no matter what. Emitting them as PASS would launder our own repair into
    evidence, which is the exact failure this project exists to avoid.
    """
    signals: list[Signal] = []
    trusted = {c.field_name for c in mrz.trustworthy_checks}

    for check in mrz.checks:
        label = _CHECK_LABELS.get(check.field_name, check.field_name)
        is_critical = check.field_name in CRITICAL_CHECKS

        if check.field_name not in trusted:
            signals.append(
                signal(
                    code=f"mrz.checksum.{check.field_name}",
                    stage=Stage.VALIDATE,
                    title=f"MRZ checksum - {label}",
                    status=SignalStatus.SKIP,
                    severity=Severity.INFO,
                    reason=(
                        f"The {label} check digit could not be evaluated. Line 2 "
                        f"was rebuilt after text recognition dropped part of its "
                        f"filler run, and this particular check verifies against "
                        f"the rebuilt section -- so it would pass regardless of "
                        f"what the document actually contains. It is reported as "
                        f"not checked rather than as passed."
                    ),
                    evidence={
                        "field": check.field_name,
                        "value": check.raw_value,
                        "stated": check.stated_digit,
                        "computed": check.computed_digit,
                        "excluded_because": "line 2 was reconstructed",
                    },
                )
            )
            continue

        if check.valid:
            signals.append(
                signal(
                    code=f"mrz.checksum.{check.field_name}",
                    stage=Stage.VALIDATE,
                    title=f"MRZ checksum - {label}",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason=(
                        f"The MRZ check digit for the {label} is correct "
                        f"(computed {check.computed_digit}, document states "
                        f"{check.stated_digit})."
                    ),
                    evidence={
                        "field": check.field_name,
                        "value": check.raw_value,
                        "stated": check.stated_digit,
                        "computed": check.computed_digit,
                    },
                )
            )
        else:
            signals.append(
                signal(
                    code=f"mrz.checksum.{check.field_name}",
                    stage=Stage.VALIDATE,
                    title=f"MRZ checksum - {label}",
                    status=SignalStatus.FAIL,
                    severity=Severity.CRITICAL if is_critical else Severity.HIGH,
                    # Check digit arithmetic is deterministic. If OCR read the
                    # characters correctly, this result is certain -- so the
                    # only uncertainty is upstream, and we say so rather than
                    # hedging the score.
                    confidence=0.95,
                    reason=(
                        f"The MRZ check digit for the {label} does not match its data. "
                        f"The document states {check.stated_digit}, but the value "
                        f"{check.raw_value!r} computes to {check.computed_digit}. "
                        f"This is what an edited field looks like when the check "
                        f"digits were not recalculated -- or, less often, an OCR "
                        f"misread of the MRZ band."
                    ),
                    evidence={
                        "field": check.field_name,
                        "value": check.raw_value,
                        "stated": check.stated_digit,
                        "computed": check.computed_digit,
                    },
                )
            )

    return signals


def validate_dates(mrz: MRZData, today: date | None = None) -> list[Signal]:
    """Expiry, birth-date plausibility, and validity-period sanity."""
    today = today or date.today()
    signals: list[Signal] = []

    # --- expiry ---
    if mrz.date_of_expiry is None:
        signals.append(
            signal(
                code="passport.expiry.unreadable",
                stage=Stage.VALIDATE,
                title="Expiry date",
                status=SignalStatus.ERROR,
                severity=Severity.MEDIUM,
                reason="The expiry date could not be read as a valid calendar date.",
            )
        )
    elif mrz.date_of_expiry < today:
        days = (today - mrz.date_of_expiry).days
        signals.append(
            signal(
                code="passport.expiry.expired",
                stage=Stage.VALIDATE,
                title="Expiry date",
                status=SignalStatus.FAIL,
                # Expiry is a validity problem, not evidence of forgery. An
                # expired passport is usually a genuine passport, so the fraud
                # severity stays moderate -- but `blocking` stops it being
                # auto-accepted on the strength of that low score.
                severity=Severity.MEDIUM,
                blocking=True,
                reason=(
                    f"This passport expired on {mrz.date_of_expiry.isoformat()}, "
                    f"{days} days ago. The document is genuine-looking but no "
                    f"longer valid for identification."
                ),
                evidence={"expiry": mrz.date_of_expiry.isoformat(), "days_expired": days},
            )
        )
    elif (mrz.date_of_expiry - today).days <= EXPIRY_ADVISORY_DAYS:
        days = (mrz.date_of_expiry - today).days
        signals.append(
            signal(
                code="passport.expiry.expiring_soon",
                stage=Stage.VALIDATE,
                title="Expiry date",
                status=SignalStatus.WARN,
                severity=Severity.LOW,
                reason=(
                    f"This passport expires in {days} days "
                    f"({mrz.date_of_expiry.isoformat()}). Many countries require "
                    f"at least six months of remaining validity."
                ),
                evidence={"expiry": mrz.date_of_expiry.isoformat(), "days_remaining": days},
            )
        )
    else:
        signals.append(
            signal(
                code="passport.expiry.valid",
                stage=Stage.VALIDATE,
                title="Expiry date",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"Valid until {mrz.date_of_expiry.isoformat()} "
                    f"({(mrz.date_of_expiry - today).days} days remaining)."
                ),
                evidence={"expiry": mrz.date_of_expiry.isoformat()},
            )
        )

    # --- date of birth plausibility ---
    if mrz.date_of_birth is None:
        signals.append(
            signal(
                code="passport.dob.unreadable",
                stage=Stage.VALIDATE,
                title="Date of birth",
                status=SignalStatus.ERROR,
                severity=Severity.MEDIUM,
                reason="The date of birth could not be read as a valid calendar date.",
            )
        )
    else:
        age_days = (today - mrz.date_of_birth).days
        age_years = age_days / 365.25

        if mrz.date_of_birth > today:
            signals.append(
                signal(
                    code="passport.dob.future",
                    stage=Stage.VALIDATE,
                    title="Date of birth",
                    status=SignalStatus.FAIL,
                    severity=Severity.CRITICAL,
                    blocking=True,
                    reason=(
                        f"The date of birth {mrz.date_of_birth.isoformat()} is in the "
                        f"future. This value cannot be correct."
                    ),
                    evidence={"dob": mrz.date_of_birth.isoformat()},
                )
            )
        elif age_years > MAX_PLAUSIBLE_AGE_YEARS:
            signals.append(
                signal(
                    code="passport.dob.implausible",
                    stage=Stage.VALIDATE,
                    title="Date of birth",
                    status=SignalStatus.FAIL,
                    severity=Severity.HIGH,
                    reason=(
                        f"The date of birth {mrz.date_of_birth.isoformat()} implies an "
                        f"age of {age_years:.0f} years, beyond any plausible value. "
                        f"The century digits were likely misread, or the field was edited."
                    ),
                    evidence={"dob": mrz.date_of_birth.isoformat(), "age_years": round(age_years, 1)},
                )
            )
        else:
            signals.append(
                signal(
                    code="passport.dob.plausible",
                    stage=Stage.VALIDATE,
                    title="Date of birth",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason=(
                        f"Date of birth {mrz.date_of_birth.isoformat()} gives an age of "
                        f"{age_years:.0f} years, which is plausible."
                    ),
                    evidence={"dob": mrz.date_of_birth.isoformat(), "age_years": round(age_years, 1)},
                )
            )

        # Validity period sanity: expiry far beyond any issuance rule suggests
        # an edited expiry year.
        if mrz.date_of_expiry and mrz.date_of_expiry > today:
            span_years = (mrz.date_of_expiry - today).days / 365.25
            if span_years > MAX_PLAUSIBLE_VALIDITY_YEARS:
                signals.append(
                    signal(
                        code="passport.validity.implausible_span",
                        stage=Stage.VALIDATE,
                        title="Validity period",
                        status=SignalStatus.WARN,
                        severity=Severity.MEDIUM,
                        reason=(
                            f"The expiry date is {span_years:.1f} years away. Passports "
                            f"are issued for at most 10 years, so an expiry this far out "
                            f"suggests the year was altered."
                        ),
                        evidence={"years_remaining": round(span_years, 1)},
                    )
                )

    return signals


def validate_structure(mrz: MRZData) -> list[Signal]:
    """Structural conformance of the MRZ itself, independent of check digits."""
    signals: list[Signal] = []

    if mrz.parse_errors:
        signals.append(
            signal(
                code="mrz.structure.malformed",
                stage=Stage.VALIDATE,
                title="MRZ structure",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                reason=(
                    "The MRZ did not fully conform to the ICAO 9303 layout: "
                    + "; ".join(mrz.parse_errors)
                ),
                evidence={"errors": mrz.parse_errors},
            )
        )

    if len(mrz.raw_line1) != 44 or len(mrz.raw_line2) != 44:
        signals.append(
            signal(
                code="mrz.structure.line_length",
                stage=Stage.VALIDATE,
                title="MRZ line length",
                status=SignalStatus.WARN,
                severity=Severity.LOW,
                reason=(
                    f"A TD3 passport MRZ has two lines of exactly 44 characters; "
                    f"this document produced {len(mrz.raw_line1)} and "
                    f"{len(mrz.raw_line2)}. Usually a cropped scan or OCR dropout."
                ),
                evidence={"line1_len": len(mrz.raw_line1), "line2_len": len(mrz.raw_line2)},
            )
        )

    if mrz.sex == "":
        signals.append(
            signal(
                code="mrz.structure.sex_field",
                stage=Stage.VALIDATE,
                title="MRZ sex field",
                status=SignalStatus.WARN,
                severity=Severity.LOW,
                reason="The MRZ sex field is not one of the expected values.",
            )
        )

    return signals


def validate_passport(mrz: MRZData | None, fields: ExtractedFields | None = None) -> list[Signal]:
    """
    Run the full passport rule set.

    A missing MRZ is reported as ERROR, never FAIL: many legitimate passport
    photographs simply do not include the MRZ band, and treating a bad crop as
    a forgery would be both wrong and unfair to the holder.
    """
    if mrz is None:
        return [
            signal(
                code="mrz.not_found",
                stage=Stage.VALIDATE,
                title="MRZ detection",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                # Blocking. On a passport the MRZ is not one check among many --
                # it is the only part of the document that verifies itself, and
                # every content check in this rulebook reads from it. Without
                # it, nothing about the document's contents has been checked at
                # all, and a low score means "we found nothing", not "we looked
                # and it was fine".
                #
                # The severity stays HIGH rather than CRITICAL because an
                # unreadable MRZ is usually a cropped or blurred scan, not a
                # forgery. The score should say "unverified", not "suspected
                # fake"; the blocking flag is what prevents acceptance.
                blocking=True,
                reason=(
                    "No machine-readable zone was found, so none of the passport "
                    "content checks could run -- the document number, date of "
                    "birth and expiry date were never verified. This is usually a "
                    "cropped or blurred scan rather than a sign of forgery, so it "
                    "is not scored as fraud, but the document cannot be accepted "
                    "on this assessment. Re-scan with the full bottom edge of the "
                    "data page visible."
                ),
            )
        ]

    signals: list[Signal] = []
    signals.extend(validate_structure(mrz))
    signals.extend(validate_mrz_checks(mrz))
    signals.extend(validate_dates(mrz))
    return signals
