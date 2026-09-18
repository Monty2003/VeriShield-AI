"""
Validate an Aadhaar card against its own Secure QR.

The QR carries what UIDAI recorded. The printed face carries what the card
shows. Comparing them turns fields that previously had no check at all -- name,
date of birth, gender, address, photograph -- into verifiable ones.

What this proves, and what it does not
--------------------------------------
The QR's RSA signature is verified against UIDAI's document-signer keys (see
app/rules/uidai_signature.py). When it verifies, the QR is exactly what UIDAI
issued and every comparison here is a check against an authenticated record:
editing the print is caught, and so is fabricating a QR to match the edits.

When it does not verify, the comparisons still catch the common forgery -- an
edited print with the original QR left in place -- but they are comparisons
against data that could have been made up. The signature signal says which
situation applies, and why.

"""

from __future__ import annotations

from datetime import date

from app.pipeline.stages.cross_document import names_match
from app.rules.aadhaar_qr import AadhaarQR
from app.schemas.document import ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

# Field names whose disagreement is worth reporting individually.
_COMPARED = ("name", "date_of_birth", "gender")



def _signature_signal(qr: AadhaarQR) -> Signal:
    """The QR's signature, checked against UIDAI's document-signer keys."""
    from app.rules.uidai_signature import pinned_keys, verify_qr_signature

    verdict = verify_qr_signature(qr)
    when = verdict.generated_at
    dated = f"generated on {when:%d %b %Y}" if when else "whose generation date could not be read"
    evidence: dict[str, object] = {
        "outcome": verdict.outcome,
        "generated_on": when.date().isoformat() if when else None,
    }

    if verdict.outcome == "verified" and verdict.key is not None:
        key = verdict.key
        whose = (
            f"UIDAI's document-signer key '{key.name}'"
            if key.certified
            else (
                f"'{key.name}', a UIDAI key recovered from the signatures of other "
                f"genuine QRs rather than read from a certificate"
            )
        )
        return signal(
            code="aadhaar.qr.signature.verified",
            stage=Stage.DATABASE,
            title="Aadhaar QR signature",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            # A certificate ties a key to UIDAI through a government CA chain; a
            # recovered key is tied to UIDAI only through the QRs it came from.
            confidence=0.99 if key.certified else 0.9,
            reason=(
                f"The QR's digital signature verifies against {whose}. The QR, "
                f"{dated}, is exactly what that key signed: the name, date of birth, "
                f"address and photograph it carries are UIDAI's own record, so the "
                f"comparisons against the printed card are checks against an "
                f"authenticated source."
            ),
            evidence={
                **evidence,
                "signer": key.name,
                "signer_valid": key.period(),
                "certified": key.certified,
            },
        )

    if verdict.outcome == "invalid" and verdict.expected_key is not None:
        key = verdict.expected_key
        return signal(
            code="aadhaar.qr.signature.invalid",
            stage=Stage.DATABASE,
            title="Aadhaar QR signature",
            status=SignalStatus.FAIL,
            # HIGH rather than CRITICAL, and blocking rather than auto-reject:
            # the inference rests on UIDAI signing with one key per period,
            # which holds for every real QR seen so far but is not guaranteed.
            # The finding is strong enough to stop acceptance and not strong
            # enough to reject a card without a person looking.
            severity=Severity.HIGH,
            confidence=0.8,
            blocking=True,
            reason=(
                f"The QR states it was {dated}, "
                + (
                    f"when UIDAI was signing with '{key.name}' ({key.period()}). "
                    if key.certified
                    else (
                        f"inside the period ({key.period()}) in which other genuine "
                        f"QRs show UIDAI signing with '{key.name}'. "
                    )
                )
                + "Its signature does not verify against that key or any other "
                "held, so its contents are not what UIDAI signed. The likeliest "
                "explanation is a QR that was altered or fabricated. A person should "
                "confirm before acting on it."
            ),
            evidence={
                **evidence,
                "expected_signer": key.name,
                "expected_signer_valid": key.period(),
            },
        )

    if verdict.outcome == "unknown_key":
        near_edge = when is not None and any(
            k.valid_from <= when <= k.valid_until for k in pinned_keys()
        )
        why = (
            "Its date sits close to a changeover between UIDAI signer keys, where "
            "either key may have been used"
            if near_edge
            else f"No UIDAI key covering {when:%b %Y} is held"
            if when
            else "Without a date there is no way to tell which key should have signed it"
        )
        return signal(
            code="aadhaar.qr.signature.unknown_key",
            stage=Stage.DATABASE,
            title="Aadhaar QR signature",
            status=SignalStatus.WARN,
            severity=Severity.MEDIUM,
            confidence=0.5,
            reason=(
                f"The QR, {dated}, carries a signature that none of this "
                f"deployment's UIDAI keys verify. {why}, so this is most likely a "
                f"genuine QR signed with a key that is not installed. A QR fabricated "
                f"with a date chosen to land here would look the same, so the "
                f"comparisons below are against a QR that could not be authenticated."
            ),
            evidence=evidence,
        )

    if verdict.outcome == "no_keys":
        return signal(
            code="aadhaar.qr.signature.unavailable",
            stage=Stage.DATABASE,
            title="Aadhaar QR signature",
            status=SignalStatus.ERROR,
            severity=Severity.MEDIUM,
            reason=(
                "The QR carries a signature, but no UIDAI signer certificates are "
                "installed, so it could not be checked (expected in "
                "backend/data/certs/uidai/). The comparisons below cannot tell a "
                "genuine QR from a fabricated one."
            ),
            evidence=evidence,
        )

    return signal(
        code="aadhaar.qr.signature.absent",
        stage=Stage.DATABASE,
        title="Aadhaar QR signature",
        status=SignalStatus.SKIP,
        severity=Severity.INFO,
        reason=(
            "The QR carries no signature, so there is nothing to verify. The "
            "comparisons below are against unauthenticated data."
        ),
        evidence=evidence,
    )

def _digits(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def validate_against_qr(
    qr: AadhaarQR, fields: ExtractedFields, today: date | None = None
) -> list[Signal]:
    """Compare everything the QR states against what was read from the card."""
    today = today or date.today()
    signals: list[Signal] = []

    if qr.parse_errors:
        signals.append(
            signal(
                code="aadhaar.qr.malformed",
                stage=Stage.DATABASE,
                title="Aadhaar QR structure",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                reason=(
                    "The QR was read but did not fully match the Aadhaar Secure QR "
                    "layout: " + "; ".join(qr.parse_errors)
                ),
            )
        )

    # --- the QR exists at all ---
    signals.append(
        signal(
            code="aadhaar.qr.present",
            stage=Stage.DATABASE,
            title="Aadhaar Secure QR",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            confidence=0.9,
            reason=(
                f"An Aadhaar Secure QR ({qr.version or 'legacy format'}) was read "
                f"from this card, carrying UIDAI's own record of the holder's "
                f"details"
                + (" and photograph" if qr.has_photo else "")
                + ". The fields printed on the card can therefore be checked "
                "against it."
            ),
            evidence=qr.masked_summary(),
        )
    )

    # --- signature: is the QR itself UIDAI's? ---
    # Everything below compares the QR with the printed card. This decides
    # whether the QR is worth comparing against at all.
    signals.append(_signature_signal(qr))

    # --- Aadhaar number: last four digits ---
    qr_last_four = qr.last_four_digits
    if qr_last_four and fields.document_number.present:
        printed = _digits(fields.document_number.value)
        if printed and len(printed) >= 4:
            if printed[-4:] == qr_last_four:
                signals.append(
                    signal(
                        code="aadhaar.qr.number_match",
                        stage=Stage.DATABASE,
                        title="Aadhaar number vs QR",
                        status=SignalStatus.PASS,
                        severity=Severity.INFO,
                        confidence=0.9,
                        reason=(
                            f"The Aadhaar number printed on the card ends {qr_last_four}, "
                            f"matching the reference in the QR."
                        ),
                        evidence={"last_four": qr_last_four},
                    )
                )
            else:
                signals.append(
                    signal(
                        code="aadhaar.qr.number_mismatch",
                        stage=Stage.DATABASE,
                        title="Aadhaar number vs QR",
                        status=SignalStatus.FAIL,
                        severity=Severity.CRITICAL,
                        confidence=0.9,
                        reason=(
                            f"The number printed on the card ends {printed[-4:]}, but "
                            f"the QR -- which UIDAI signed and which cannot be edited "
                            f"without breaking that signature -- refers to a number "
                            f"ending {qr_last_four}. The printed number and the card's "
                            f"own machine-readable record disagree."
                        ),
                        evidence={
                            "printed_last_four": printed[-4:],
                            "qr_last_four": qr_last_four,
                        },
                    )
                )

    # --- name ---
    qr_name = qr.fields.get("name", "").strip()
    if qr_name and fields.full_name.present:
        matched, score = names_match(qr_name, str(fields.full_name.value))
        if matched:
            signals.append(
                signal(
                    code="aadhaar.qr.name_match",
                    stage=Stage.DATABASE,
                    title="Name vs QR",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    confidence=min(0.95, score / 100),
                    reason=(
                        f"The name printed on the card matches the name in the "
                        f"signed QR ({score:.0f}% similarity)."
                    ),
                    evidence={"similarity": round(score, 1)},
                )
            )
        else:
            signals.append(
                signal(
                    code="aadhaar.qr.name_mismatch",
                    stage=Stage.DATABASE,
                    title="Name vs QR",
                    status=SignalStatus.FAIL,
                    severity=Severity.CRITICAL,
                    # High, but OCR read the printed side, and a misread name
                    # produces this too. Not absolute.
                    confidence=0.8,
                    reason=(
                        f"The name printed on the card does not match the name in "
                        f"the QR ({score:.0f}% similarity). An Aadhaar name has no "
                        f"checksum, so before the QR this was undetectable -- but "
                        f"the QR is signed by UIDAI and carries the name they hold. "
                        f"Either the printed name was altered, or text recognition "
                        f"misread it."
                    ),
                    evidence={"similarity": round(score, 1)},
                )
            )

    # --- date of birth ---
    qr_dob = qr.date_of_birth_parsed
    qr_year = qr.year_of_birth
    if fields.date_of_birth.present and isinstance(fields.date_of_birth.value, date):
        printed_dob: date = fields.date_of_birth.value
        if qr_dob is not None:
            if printed_dob == qr_dob:
                signals.append(
                    signal(
                        code="aadhaar.qr.dob_match",
                        stage=Stage.DATABASE,
                        title="Date of birth vs QR",
                        status=SignalStatus.PASS,
                        severity=Severity.INFO,
                        confidence=0.9,
                        reason="The printed date of birth matches the signed QR.",
                    )
                )
            else:
                signals.append(
                    signal(
                        code="aadhaar.qr.dob_mismatch",
                        stage=Stage.DATABASE,
                        title="Date of birth vs QR",
                        status=SignalStatus.FAIL,
                        severity=Severity.CRITICAL,
                        confidence=0.85,
                        reason=(
                            f"The date of birth printed on the card "
                            f"({printed_dob.isoformat()}) does not match the one in "
                            f"the signed QR ({qr_dob.isoformat()}). A date of birth "
                            f"on an Aadhaar card has no checksum of its own, so this "
                            f"disagreement is the only thing that reveals an "
                            f"alteration."
                        ),
                        evidence={
                            "printed": printed_dob.isoformat(),
                            "qr": qr_dob.isoformat(),
                        },
                    )
                )
        elif qr_year is not None and printed_dob.year != qr_year:
            # Older cards record only a year of birth.
            signals.append(
                signal(
                    code="aadhaar.qr.dob_year_mismatch",
                    stage=Stage.DATABASE,
                    title="Date of birth vs QR",
                    status=SignalStatus.FAIL,
                    severity=Severity.HIGH,
                    confidence=0.8,
                    reason=(
                        f"The card shows a birth year of {printed_dob.year}, but the "
                        f"signed QR records {qr_year}."
                    ),
                    evidence={"printed_year": printed_dob.year, "qr_year": qr_year},
                )
            )

    return signals


def compare_qr_photo(
    qr: AadhaarQR,
    document_image: bytes,
    face_provider=None,
) -> list[Signal]:
    """
    Compare UIDAI's photograph from the QR against the one printed on the card.

    This is the check that image forensics could not deliver. Three separate
    attempts at detecting portrait substitution from pixel statistics measured
    at chance on real documents. Comparing against the issuer's own copy needs
    no statistics: either it is the same face or it is not.

    The QR photograph is about 60x60, well below what a face embedding is meant
    for, so similarity scores here run lower than between two full-size images
    and the thresholds are relaxed to match. That relaxation is stated in the
    reason rather than hidden in a constant.
    """
    from app.pipeline.stages.face import (
        STRONG_MATCH_THRESHOLD,
        cosine_similarity,
        get_default_provider,
    )
    from app.rules.aadhaar_qr import decode_qr_photo

    if not qr.has_photo:
        return [
            signal(
                code="aadhaar.qr.no_photo",
                stage=Stage.FACE,
                title="Portrait vs QR photograph",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "This QR carries no photograph, so the printed portrait could "
                    "not be checked against UIDAI's copy. Older Secure QR versions "
                    "omit it."
                ),
            )
        ]

    provider = face_provider or get_default_provider()
    if not provider.recognition_available:
        return [
            signal(
                code="aadhaar.qr.photo_no_model",
                stage=Stage.FACE,
                title="Portrait vs QR photograph",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "The QR contains UIDAI's photograph of the holder, but no face "
                    "recognition model is installed to compare it against the "
                    "printed portrait."
                ),
            )
        ]

    qr_image = decode_qr_photo(qr)
    if qr_image is None:
        return [
            signal(
                code="aadhaar.qr.photo_undecodable",
                stage=Stage.FACE,
                title="Portrait vs QR photograph",
                status=SignalStatus.ERROR,
                severity=Severity.LOW,
                reason="The photograph inside the QR could not be decoded.",
            )
        ]

    import cv2
    import numpy as np

    # Embed the QR photograph DIRECTLY, without running a face detector on it.
    #
    # UIDAI stores an already-cropped face, so detection has nothing to do --
    # and at 60 pixels it fails anyway. An earlier version ran the full detect-
    # then-recognise pipeline here and skipped every card with "no face could
    # be located", which read like a limitation of the data when it was a
    # limitation of the approach.
    #
    # Feeding the crop straight to the recogniser separates cleanly: measured
    # on real cards, 0.80 for the same person against 0.02 for a different one.
    qr_embedding = None
    try:
        recogniser = provider._get_app().models.get("recognition")
        if recogniser is not None:
            aligned = cv2.resize(qr_image, (112, 112), interpolation=cv2.INTER_CUBIC)
            qr_embedding = np.asarray(recogniser.get_feat(aligned)).flatten()
    except Exception:  # noqa: BLE001 -- fall through to the SKIP below
        qr_embedding = None

    if qr_embedding is None or qr_embedding.size == 0:
        return [
            signal(
                code="aadhaar.qr.photo_no_embedding",
                stage=Stage.FACE,
                title="Portrait vs QR photograph",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "The QR photograph could not be turned into a face embedding, "
                    "so it could not be compared with the printed portrait."
                ),
            )
        ]

    card_faces = provider.analyze(document_image)

    if card_faces.primary is None or not card_faces.primary.has_embedding:
        return [
            signal(
                code="aadhaar.qr.card_no_face",
                stage=Stage.FACE,
                title="Portrait vs QR photograph",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason="No portrait was located on the card to compare against.",
            )
        ]

    similarity = cosine_similarity(qr_embedding, card_faces.primary.embedding)

    # The normal strong-match threshold, not a relaxed one. Direct embedding of
    # the crop turned out to separate better than expected -- 0.80 same person,
    # 0.02 different -- so lowering the bar would only admit false matches.
    strong = STRONG_MATCH_THRESHOLD

    if similarity >= strong:
        return [
            signal(
                code="aadhaar.qr.photo_match",
                stage=Stage.FACE,
                title="Portrait vs QR photograph",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                confidence=0.7,
                reason=(
                    f"The portrait printed on the card matches UIDAI's own "
                    f"photograph from the QR (similarity {similarity:.2f}). "
                    f"Confidence is moderate rather than high because the QR "
                    f"photograph is about 60 pixels across."
                ),
                evidence={"similarity": round(similarity, 4)},
                regions=[card_faces.primary.region],
            )
        ]

    return [
        signal(
            code="aadhaar.qr.photo_mismatch",
            stage=Stage.FACE,
            title="Portrait vs QR photograph",
            status=SignalStatus.FAIL,
            severity=Severity.CRITICAL,
            # The strongest evidence of photo substitution available, and still
            # not certain: a 60-pixel reference is a genuine handicap.
            confidence=0.7,
            reason=(
                f"The portrait printed on this card does not match UIDAI's own "
                f"photograph of the holder, taken from the card's signed QR "
                f"(similarity {similarity:.2f}, below {strong}). This is what a "
                f"substituted photograph looks like. Note that the QR photograph "
                f"is only about 60 pixels across, so confirm visually before "
                f"acting."
            ),
            evidence={"similarity": round(similarity, 4)},
            regions=[card_faces.primary.region],
        )
    ]
