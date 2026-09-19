"""
Pipeline orchestrator.

Runs the stages in order and collects their Signals. The orchestrator makes
exactly one policy decision of its own: a stage that raises must not abort the
verification. Everything else is delegated.

That policy matters. If the forensics stage crashes on an unusual image, the
MRZ checksums are still perfectly good evidence, and a reviewer should get
them along with a clear note that forensics did not run -- rather than a 500
and nothing at all. Partial evidence, honestly labelled, beats no evidence.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.pipeline.stages import classify as classify_stage
from app.pipeline.stages import face as face_stage
from app.pipeline.stages import forensics as forensics_stage
from app.pipeline.stages import ocr as ocr_stage
from app.pipeline.stages.extract import extract_fields
from app.registry.authority import RegistryProvider, check_registry
from app.risk.engine import assess_case, assess_document
from app.rules.registry import validate_for_type
from app.schemas.document import (
    DocumentAnalysis,
    DocumentSide,
    DocumentType,
    VerificationResult,
)
from app.schemas.signals import (
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)

# Stages we expect to contribute evidence for a document image. Used to
# compute the confidence figure attached to every assessment.
EXPECTED_STAGES = (
    Stage.CLASSIFY,
    Stage.OCR,
    Stage.EXTRACT,
    Stage.VALIDATE,
    Stage.FORENSICS,
)


def _timed(fn, timings: dict[str, float], key: str):
    """Run fn, record its wall time, and convert any crash into a Signal."""
    start = time.perf_counter()
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001 -- a broken stage must not end the case
        return None, signal(
            code=f"{key}.stage_error",
            stage=Stage[key.upper()] if key.upper() in Stage.__members__ else Stage.INGEST,
            title=f"{key.title()} stage",
            status=SignalStatus.ERROR,
            severity=Severity.MEDIUM,
            reason=(
                f"The {key} stage failed with an internal error and produced no "
                f"evidence: {type(exc).__name__}: {exc}. The rest of the "
                f"assessment continued without it."
            ),
        )
    finally:
        timings[key] = round((time.perf_counter() - start) * 1000, 2)


def analyze_document(
    image_bytes: bytes,
    filename: str = "document",
    *,
    ocr_provider: ocr_stage.OCRProvider | None = None,
    face_provider: face_stage.FaceProvider | None = None,
    registry: RegistryProvider | None = None,
    face_result: object | None = None,
    enable_copy_move: bool = False,
    declared_type: DocumentType | None = None,
) -> DocumentAnalysis:
    """
    Run the full single-document pipeline.

    `declared_type` lets a caller state the document type instead of relying on
    classification -- useful when the type is already known from the workflow
    (an upload slot labelled "passport"), which removes a whole class of
    misrouting error.
    """
    timings: dict[str, float] = {}
    signals: list[Signal] = []

    analysis = DocumentAnalysis(
        document_id=str(uuid.uuid4()),
        filename=filename,
    )

    # --- ingest: dimensions, and a sanity check on what we were handed ---
    def _ingest():
        from app.core.imaging import decode_image

        return decode_image(image_bytes).shape[:2]

    dims, err = _timed(_ingest, timings, "ingest")
    if err is not None:
        signals.append(err)
        analysis.signals = signals
        analysis.risk = assess_document(signals, EXPECTED_STAGES)
        analysis.processing_ms = timings
        return analysis

    analysis.image_height, analysis.image_width = dims
    signals.append(
        signal(
            code="ingest.ok",
            stage=Stage.INGEST,
            title="Document intake",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            reason=(
                f"Received {filename} ({analysis.image_width}x{analysis.image_height} "
                f"pixels, {len(image_bytes) / 1024:.0f} KB)."
            ),
        )
    )

    # Low resolution silently degrades OCR and every content check that
    # depends on it, so it is worth saying out loud rather than letting the
    # downstream failures look like document problems.
    if min(dims) < 400:
        signals.append(
            signal(
                code="ingest.low_resolution",
                stage=Stage.INGEST,
                title="Image resolution",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                reason=(
                    f"The image is only {analysis.image_width}x{analysis.image_height}. "
                    f"Text recognition and forensic analysis both degrade sharply "
                    f"below roughly 1000px on the long edge, so findings here are "
                    f"less reliable than usual."
                ),
            )
        )

    # --- OCR ---
    provider = ocr_provider or ocr_stage.get_default_provider()
    ocr_result, err = _timed(lambda: provider.read(image_bytes), timings, "ocr")
    if err is not None:
        signals.append(err)
        ocr_result = ocr_stage.OCRResult(available=False, error=str(err.reason))
    signals.extend(ocr_stage.ocr_signals(ocr_result))

    text = ocr_result.full_text if ocr_result else ""

    # --- classify ---
    if declared_type is not None:
        analysis.document_type = declared_type
        analysis.type_confidence = 1.0
        signals.append(
            signal(
                code="classify.declared",
                stage=Stage.CLASSIFY,
                title="Document type",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"Type was declared by the caller as "
                    f"{declared_type.value.replace('_', ' ')}; automatic "
                    f"classification was not used."
                ),
            )
        )
    else:
        result, err = _timed(lambda: classify_stage.classify(text), timings, "classify")
        if err is not None:
            signals.append(err)
        else:
            doc_type, conf, side, cls_signals = result
            analysis.document_type = doc_type
            analysis.type_confidence = conf
            analysis.side = DocumentSide(side)
            signals.extend(cls_signals)

        # --- an image that is only an Aadhaar QR ---
        #
        # A close-up of the QR alone is often the only photograph of a dense
        # Secure QR that decodes, and it carries no printed wording for the
        # classifier. It was being filed as UNKNOWN, and the QR stage runs only
        # on Aadhaar images -- so the one image made to be read was never read.
        # A QR that parses as UIDAI's Secure QR format is itself the strongest
        # type evidence there is.
        if analysis.document_type == DocumentType.UNKNOWN:
            qr_side, err = _timed(
                lambda: _classify_by_aadhaar_qr(image_bytes), timings, "classify_qr"
            )
            if err is not None:
                signals.append(err)
            elif qr_side:
                signals[:] = [s for s in signals if s.code != "classify.unknown"]
                analysis.document_type = DocumentType.AADHAAR
                analysis.type_confidence = 0.95
                analysis.side = DocumentSide.BACK
                signals.extend(qr_side)

    # --- extract ---
    fields_result, err = _timed(
        lambda: extract_fields(
            text, analysis.document_type, side=analysis.side.value
        ),
        timings,
        "extract",
    )
    if err is not None:
        signals.append(err)
    else:
        analysis.fields, extract_signals, mrz_data = fields_result
        signals.extend(extract_signals)

        # --- validate (type-specific rulebook) ---
        val_signals, err = _timed(
            lambda: validate_for_type(analysis.document_type, mrz_data, analysis.fields),
            timings,
            "validate",
        )
        if err is not None:
            signals.append(err)
        else:
            signals.extend(val_signals)

    # --- Aadhaar Secure QR (Layer 8, offline authority) ---
    #
    # The only check in the pipeline that reaches outside the document. Runs
    # before the generic registry because when a QR is present it is far
    # stronger evidence than a synthetic record lookup.
    #
    # Deliberately NOT restricted to the front. On an Indian Aadhaar card the
    # QR is printed on the BACK -- the front carries the photograph and number,
    # the back the address and the QR. An earlier version skipped reverse sides
    # here and so skipped the QR on every card that had one.
    if analysis.document_type == DocumentType.AADHAAR:
        from app.rules.aadhaar import validate_aadhaar_qr

        qr_signals, err = _timed(
            # The expensive whole-image upscaling is spent only where a QR is
            # expected: not on a card front, which in the genuine set never
            # carried one that the cheaper routes missed.
            lambda: validate_aadhaar_qr(
                image_bytes,
                analysis.fields,
                thorough=analysis.side != DocumentSide.FRONT,
                # A back is already blocked as a back; the requirement is for
                # the side that carries the identity fields.
                require=settings.aadhaar_require_qr
                and analysis.side != DocumentSide.BACK,
            ),
            timings,
            "qr",
        )
        if err is not None:
            signals.append(err)
        else:
            signals.extend(qr_signals)
    elif analysis.document_type == DocumentType.CERTIFICATE:
        cert_qr, err = _timed(lambda: _certificate_qr_signals(image_bytes), timings, "qr")
        if err is not None:
            signals.append(err)
        else:
            signals.extend(cert_qr)
    elif analysis.document_type == DocumentType.PAN and analysis.side != DocumentSide.BACK:
        pan_qr, err = _timed(lambda: _pan_qr_signals(image_bytes), timings, "qr")
        if err is not None:
            signals.append(err)
        else:
            signals.extend(pan_qr)

    # --- registry (Layer 8) ---
    #
    # Runs only for the front of a document: a reverse side carries no number
    # to look up, and asking anyway would produce a "not found" that means
    # nothing except that we looked in the wrong place.
    if analysis.side != DocumentSide.BACK:
        registry_signals, err = _timed(
            lambda: check_registry(analysis.document_type, analysis.fields, registry),
            timings,
            "database",
        )
        if err is not None:
            signals.append(err)
        else:
            signals.extend(registry_signals)

    # --- face detection (Layer 6) ---
    #
    # Detection only here. Verifying WHO is presenting the document needs a
    # second image, so it belongs to the case-level flow rather than to the
    # analysis of a single document.
    # A caller that already ran detection passes the result in. Face analysis
    # is the most expensive stage in the pipeline, and verify_case needs the
    # embeddings anyway -- running it twice per document doubled case latency
    # for no additional information.
    if face_result is None:
        face_result, err = _timed(
            lambda: face_stage.detect_document_face(
                image_bytes, face_provider or face_stage.get_default_provider()
            ),
            timings,
            "face",
        )
        if err is not None:
            signals.append(err)
    else:
        timings["face"] = 0.0

    if face_result is not None:
        detection, face_signals = face_result
        signals.extend(face_signals)
        if detection.primary is not None:
            analysis.face_region = detection.primary.region

    # --- forensics ---
    forensic_signals, err = _timed(
        lambda: forensics_stage.run_forensics(
            image_bytes, enable_copy_move=enable_copy_move
        ),
        timings,
        "forensics",
    )
    if err is not None:
        signals.append(err)
    else:
        signals.extend(forensic_signals)

    # --- invariant: a document nothing validated cannot be accepted ---
    #
    # Every rulebook signal carries stage VALIDATE. If not one was emitted, no
    # content check ran at all -- because extraction crashed, or the type was
    # never determined, or a stage raised. The risk score in that situation is
    # low simply because nothing looked, and coverage alone does not catch it:
    # a document with four healthy stages out of five clears the confidence
    # floor and is accepted having had none of its contents examined.
    #
    # Observed exactly that way: an extract-stage crash produced ACCEPT at
    # 1.8/100 on a real Aadhaar card.
    if not any(s.stage == Stage.VALIDATE for s in signals):
        signals.append(
            signal(
                code="validate.did_not_run",
                stage=Stage.VALIDATE,
                title="Content validation",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    "No content validation ran on this document, so nothing about "
                    "what it says has been checked. A low risk score here means "
                    "only that no check reported a problem -- not that the "
                    "document passed. Manual verification is required."
                ),
                evidence={"stages_completed": sorted(timings)},
            )
        )

    analysis.signals = signals
    analysis.processing_ms = timings
    analysis.risk = assess_document(signals, EXPECTED_STAGES)
    return analysis


# Services that forward a link somewhere else. A QR through one of these hides
# where it ends up, which is exactly what checking a certificate link is for --
# one of 3 certificate QRs collected for this project went through one.
_LINK_REDIRECTORS = {
    "bit.ly", "tinyurl.com", "t.ly", "goo.gl", "rb.gy", "cutt.ly", "is.gd", "ow.ly",
    "buff.ly", "tiny.cc", "rebrand.ly", "shorturl.at", "s.id", "t.co", "lnkd.in",
    "qr-codes.io", "qrco.de", "qr.link", "me-qr.com", "qrfy.io", "qr.io", "linktr.ee",
}


def _certificate_qr_signals(image_bytes: bytes) -> list[Signal]:
    """
    What a certificate's QR points to. Reported for a person to follow; never fetched.

    Many course and training certificates carry a QR linking to the issuer's
    verification page -- the one real way to confirm them. The link is not
    opened here: the service stays offline, and fetching an address taken from
    an uploaded image would let any upload make this server visit any site.
    """
    from urllib.parse import urlparse

    from app.rules.aadhaar_qr import extract_qr_payloads

    found: list[Signal] = []
    for payload in extract_qr_payloads(image_bytes, thorough=False)[:2]:
        text = payload.decode("utf-8", "replace").strip()
        address = text if "://" in text else ("http://" + text if text.lower().startswith("www.") else "")
        parsed = urlparse(address) if address else None
        if parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname:
            found.append(
                signal(
                    code="certificate.qr.text",
                    stage=Stage.DATABASE,
                    title="Certificate QR code",
                    status=SignalStatus.SKIP,
                    severity=Severity.INFO,
                    reason=(
                        "A QR code was read from this certificate. It carries text "
                        "rather than a web address, so it points to nothing that "
                        "could confirm the certificate."
                    ),
                    evidence={"length": len(payload)},
                )
            )
            continue

        domain = parsed.hostname.lower().removeprefix("www.")
        insecure = parsed.scheme == "http"
        found.append(
            signal(
                code="certificate.qr.link",
                stage=Stage.DATABASE,
                title="Certificate QR code",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    f"The certificate's QR code links to {domain}. Opening it is how "
                    f"this certificate is confirmed: check that the site belongs to "
                    f"the issuer and shows the same name and details. The link was "
                    f"not opened here."
                    + (" It is not an encrypted (https) address." if insecure else "")
                ),
                # "url" is masked in the audit trail; the reviewer sees it live.
                evidence={"domain": domain, "url": address[:300], "https": not insecure},
            )
        )
        if domain in _LINK_REDIRECTORS:
            found.append(
                signal(
                    code="certificate.qr.redirect",
                    stage=Stage.DATABASE,
                    title="Certificate QR code",
                    status=SignalStatus.WARN,
                    severity=Severity.LOW,
                    reason=(
                        f"The QR goes through {domain}, a link-shortening or redirect "
                        f"service, so where it finally leads cannot be seen without "
                        f"opening it. Issuers usually link straight to their own "
                        f"site; open it with care and check where it lands."
                    ),
                    evidence={"domain": domain},
                )
            )
    return found


def _pan_qr_signals(image_bytes: bytes) -> list[Signal]:
    """
    Report a PAN card's QR without pretending to verify it.

    PAN cards issued since 2018 carry a QR on the front. Unlike UIDAI's, its
    format is not published: on four genuine cards it is a 1,496-byte payload
    that is not compressed like the Aadhaar QR and carries no readable text, so
    nothing in it can be compared with the card here. It is reported so a
    reviewer knows it is there and how to check it -- and it does not move the
    score, because an unread QR is no evidence either way.
    """
    from app.rules.aadhaar_qr import extract_qr_payloads

    if not extract_qr_payloads(image_bytes, thorough=False):
        return []
    return [
        signal(
            code="pan.qr.present",
            stage=Stage.DATABASE,
            title="PAN QR code",
            status=SignalStatus.SKIP,
            severity=Severity.INFO,
            reason=(
                "A QR code was read from this PAN card. The Income Tax "
                "Department's PAN QR format is not published, so it could not be "
                "decoded or checked here. To confirm the card, scan the QR with "
                "the department's official PAN QR Code Reader app and compare "
                "what it shows with the card."
            ),
        )
    ]


def _classify_by_aadhaar_qr(image_bytes: bytes) -> list[Signal]:
    """
    Classification signals for an image identified only by its Aadhaar QR.

    Empty when the image carries no Secure QR. The QR side is treated as the
    reverse of the card: nothing printed about the holder is in it, so it is
    withheld alone -- and answered, like any reverse side, by the front
    arriving in the same case.
    """
    from app.rules.aadhaar_qr import read_aadhaar_qr

    qr, _ = read_aadhaar_qr(image_bytes, thorough=True)
    if qr is None:
        return []
    return [
        signal(
            code="classify.reverse_side",
            stage=Stage.CLASSIFY,
            title="Document side",
            status=SignalStatus.WARN,
            severity=Severity.LOW,
            confidence=0.9,
            blocking=True,
            reason=(
                "This image shows an Aadhaar Secure QR without the printed "
                "front of the card. The QR identified it; nothing printed about "
                "the holder is in it, so it cannot be accepted on its own. "
                "Submit it together with the front of the card, and the QR will "
                "be compared with what the front prints."
            ),
            evidence={"side": "back", "cues": ["Aadhaar Secure QR"]},
        ),
        signal(
            code="classify.identified",
            stage=Stage.CLASSIFY,
            title="Document type",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            confidence=0.95,
            reason=(
                f"Identified as the QR side of an Aadhaar from its Secure QR "
                f"({qr.version or 'unversioned'}), which parsed as UIDAI's format. "
                f"No printed wording was needed."
            ),
            evidence={"type": DocumentType.AADHAAR.value, "cues": ["Aadhaar Secure QR"]},
        ),
    ]


def _cross_check_aadhaar_qr(
    analyses: list[DocumentAnalysis],
    sources: list[tuple[bytes, str]],
    face_provider: face_stage.FaceProvider | None = None,
) -> list[Signal]:
    """
    Compare an Aadhaar Secure QR found on one image against fields read from
    another image of the same card.

    Pairs the QR-bearing side with whichever side actually yielded printed
    fields. Without this, a front-and-back upload reads the QR and never
    compares it to anything.
    """
    from app.rules.aadhaar_qr import read_aadhaar_qr
    from app.rules.aadhaar_qr_validate import validate_against_qr

    aadhaar = [
        (analysis, data)
        for analysis, (data, _) in zip(analyses, sources)
        if analysis.document_type == DocumentType.AADHAAR
    ]
    if len(aadhaar) < 2:
        return []

    # The side carrying printed identity fields, and the side carrying the QR,
    # are usually different images.
    with_fields = [a for a, _ in aadhaar if a.fields.document_number.present]
    if not with_fields:
        return []

    for analysis_, data in aadhaar:
        # Same flag as the document's own QR stage, so this is answered from
        # the payload cache rather than decoded a second time.
        qr, _note = read_aadhaar_qr(data, thorough=analysis_.side != DocumentSide.FRONT)
        if qr is None:
            continue

        # Compare against the richest set of printed fields available.
        best = max(with_fields, key=lambda a: len(a.fields.populated()))
        signals = validate_against_qr(qr, best.fields)

        # The QR carries UIDAI's own photograph of the holder. Comparing it
        # against the portrait printed on the card is the only check in this
        # project that detects photo substitution -- three attempts at
        # detecting it from image statistics all measured at chance.
        #
        # The portrait lives on the FRONT and the QR on the back, so the
        # comparison needs the image that actually has a face on it.
        #
        # When no face was found on either side, the comparison still runs,
        # against the side that carries the printed fields: that is where the
        # portrait belongs, and "no portrait on the card" is a finding. An
        # earlier version skipped the check here, so a substituted photo that
        # also defeated face detection passed with nothing said at all.
        portrait_image = next(
            (
                data
                for analysis, data in aadhaar
                if analysis.face_region is not None
            ),
            next(data for analysis, data in aadhaar if analysis is best),
        )
        from app.rules.aadhaar_qr_validate import compare_qr_photo

        signals.extend(compare_qr_photo(qr, portrait_image, face_provider))

        for produced in signals:
            produced.evidence["cross_side"] = True
        return signals

    return []


def _has_both_sides(documents: list[DocumentAnalysis], doc_type: DocumentType) -> bool:
    sides = {d.side for d in documents if d.document_type == doc_type}
    return DocumentSide.FRONT in sides and DocumentSide.BACK in sides


def _satisfied_blocks(
    documents: list[DocumentAnalysis], cross_signals: list[Signal]
) -> set[str]:
    """
    Per-document blocks that the case as a whole has answered.

    A block is only lifted by what actually answers it. The reverse-side
    blocks are answered by the front being present -- they asked for the other
    side, and it is here. The unchecked-QR block is NOT answered by the back
    merely being present: only by its QR having been read and compared with the
    front. A back whose QR would not decode leaves the front exactly as
    unauthenticated as it was on its own.
    """
    satisfied: set[str] = set()
    for doc_type in {d.document_type for d in documents}:
        if _has_both_sides(documents, doc_type):
            satisfied.update(
                {
                    "classify.reverse_side",
                    "aadhaar.number.not_found",
                    "pan.number.not_found",
                    "validate.did_not_run",
                }
            )
    if any(
        s.code == "aadhaar.qr.present" and s.evidence.get("cross_side")
        for s in cross_signals
    ):
        satisfied.add("aadhaar.qr.unchecked")
    return satisfied


def verify_case(
    documents: list[tuple[bytes, str]],
    *,
    selfie: bytes | None = None,
    ocr_provider: ocr_stage.OCRProvider | None = None,
    face_provider: face_stage.FaceProvider | None = None,
    registry: RegistryProvider | None = None,
    enable_copy_move: bool = False,
) -> VerificationResult:
    """
    Run a full case: several documents assessed individually, then compared.

    Cross-document comparison is where a case becomes more than the sum of its
    documents -- two individually flawless documents that disagree about a date
    of birth are a finding neither produces alone.

    A selfie, when supplied, answers the one question the documents cannot:
    whether the person presenting them is their holder.
    """
    from app.pipeline.stages.cross_document import (
        compare_documents,
        compare_faces_across_documents,
    )

    result = VerificationResult(case_id=str(uuid.uuid4()))
    faces = face_provider or face_stage.get_default_provider()

    # Detect once per document, up front. The result feeds both the individual
    # assessment and the portrait comparison below.
    detections = [
        face_stage.detect_document_face(image_bytes, faces)
        for image_bytes, _ in documents
    ]

    for (image_bytes, filename), detection in zip(documents, detections):
        result.documents.append(
            analyze_document(
                image_bytes,
                filename,
                ocr_provider=ocr_provider,
                face_provider=faces,
                registry=registry,
                face_result=detection,
                enable_copy_move=enable_copy_move,
            )
        )

    cross_signals = compare_documents(result.documents)

    # --- Aadhaar QR from one side against the print on the other ---
    #
    # This is where the QR earns its place. On an Indian Aadhaar the QR is on
    # the BACK and the name, number and date of birth are on the FRONT, so
    # neither image alone can be checked against UIDAI's record -- only the
    # pair can. A single-document upload gets the QR read and reported; a
    # front-and-back pair gets it actually verified against what is printed.
    cross_signals.extend(
        _cross_check_aadhaar_qr(result.documents, documents, faces)
    )

    # --- portraits across documents ---
    #
    # The strongest cross-document check there is, and the reason it runs even
    # when the names already agree: a name can be transliterated, reordered or
    # mistyped into agreement, and this module deliberately tolerates all of
    # that. A face cannot be spelled differently.
    portraits: list[tuple[str, object]] = []
    for doc, (face_detection, _) in zip(result.documents, detections):
        if face_detection.primary is not None:
            portraits.append(
                (doc.document_type.value.replace("_", " "), face_detection.primary)
            )

    if portraits:
        cross_signals.extend(compare_faces_across_documents(portraits))

    # --- presented face against the documents ---
    if selfie is not None:
        matched_any = False
        for doc, (image_bytes, _) in zip(result.documents, documents):
            if doc.face_region is None:
                continue
            matched_any = True
            cross_signals.extend(
                face_stage.verify_faces(image_bytes, selfie, faces)
            )
            # One comparison is enough: the documents were already checked
            # against each other above, so repeating the selfie against every
            # portrait would multiply the same evidence rather than add to it.
            break

        if not matched_any:
            cross_signals.append(
                signal(
                    code="face.no_document_portrait",
                    stage=Stage.FACE,
                    title="Face verification",
                    status=SignalStatus.ERROR,
                    severity=Severity.HIGH,
                    blocking=True,
                    reason=(
                        "A face was submitted for comparison, but none of the "
                        "documents in this case carries a portrait to compare it "
                        "against, so the presenter's identity was not verified."
                    ),
                )
            )

    result.cross_document_signals = cross_signals

    # Blocks a single image raises that the case as a whole has answered --
    # see _satisfied_blocks for which, and why only those.
    satisfied = _satisfied_blocks(result.documents, cross_signals)
    complete = {
        d.document_type
        for d in result.documents
        if _has_both_sides(result.documents, d.document_type)
    }
    for doc_type in sorted(complete, key=lambda t: t.value):
        cross_signals.append(
            signal(
                code="cross.both_sides_present",
                stage=Stage.CROSS_DOC,
                title="Document completeness",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"Both the front and the reverse of the "
                    f"{doc_type.value.replace('_', ' ')} were submitted, so the "
                    f"identity fields and the machine-readable side are both "
                    f"available."
                ),
            )
        )

    result.overall_risk = assess_case(
        [d.risk for d in result.documents if d.risk],
        result.cross_document_signals,
        satisfied_blocks=satisfied,
    )
    result.completed_at = datetime.now(timezone.utc)
    return result
