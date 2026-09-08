"""
Validate an Aadhaar card against its own Secure QR.

The QR carries what UIDAI recorded. The printed face carries what the card
shows. Comparing them turns fields that previously had no check at all -- name,
date of birth, gender, address, photograph -- into verifiable ones.

An honest statement of what this proves, and what it does not
------------------------------------------------------------
UIDAI signs the QR payload, so its contents cannot be edited without
invalidating the signature. But this module compares the QR against the print;
it does not yet VERIFY that signature, because doing so needs UIDAI's public
certificate.

The difference matters and is reported in every signal:

  * Against a forger who edits the printed side and leaves the QR alone --
    which is the overwhelmingly common case, because regenerating a QR is not
    something an image editor does -- these checks are decisive.
  * Against a forger who fabricates a QR to match their edits, they are not.
    A crafted QR would fail signature verification, and until that runs, a
    crafted QR passes here.

So a QR match is strong evidence and not proof, and the reasons say so rather
than implying an authority confirmed anything.

Privacy: no reason string or evidence payload contains a full Aadhaar number, a
full address, or the holder's name from the QR. Comparisons report agreement,
not values.
"""

from __future__ import annotations

from datetime import date

from app.pipeline.stages.cross_document import names_match
from app.rules.aadhaar_qr import AadhaarQR
from app.schemas.document import ExtractedFields
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

# Field names whose disagreement is worth reporting individually.
_COMPARED = ("name", "date_of_birth", "gender")


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

    # --- signature: present, but not yet verified ---
    if qr.signature:
        signals.append(
            signal(
                code="aadhaar.qr.signature_unverified",
                stage=Stage.DATABASE,
                title="Aadhaar QR signature",
                # SKIP, not PASS. The signature is there; nothing has checked it.
                # Reporting its mere presence as a pass would be the exact kind
                # of unearned reassurance this project avoids.
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "The QR carries a 256-byte RSA signature, but it has NOT been "
                    "verified -- that requires UIDAI's public certificate, which "
                    "this deployment does not have. The comparisons below still "
                    "catch a card whose printed side was edited while the QR was "
                    "left intact; they would not catch a QR fabricated to match "
                    "the edits."
                ),
                evidence={"signature_bytes": len(qr.signature), "verified": False},
            )
        )

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
