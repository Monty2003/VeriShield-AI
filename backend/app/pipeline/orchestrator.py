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
            lambda: validate_aadhaar_qr(image_bytes, analysis.fields), timings, "qr"
        )
        if err is not None:
            signals.append(err)
        else:
            signals.extend(qr_signals)

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

    for _analysis, data in aadhaar:
        qr, _note = read_aadhaar_qr(data)
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
        portrait_image = next(
            (
                data
                for analysis, data in aadhaar
                if analysis.face_region is not None
            ),
            None,
        )
        if portrait_image is not None:
            from app.rules.aadhaar_qr_validate import compare_qr_photo

            signals.extend(compare_qr_photo(qr, portrait_image, face_provider))

        for produced in signals:
            produced.evidence["cross_side"] = True
        return signals

    return []


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

    # If the case contains both faces of a document type, the reverse-side
    # block has been answered -- the caller supplied exactly what it asked for.
    satisfied: set[str] = set()
    sides_by_type: dict[DocumentType, set[DocumentSide]] = {}
    for doc in result.documents:
        sides_by_type.setdefault(doc.document_type, set()).add(doc.side)
    for doc_type, sides in sides_by_type.items():
        if DocumentSide.FRONT in sides and DocumentSide.BACK in sides:
            # Both blocks a reverse-side image raises are answered by the
            # front being present: it asked for the other side, and it is
            # here, carrying the identity fields the back does not print.
            satisfied.update(
                {
                    "classify.reverse_side",
                    "aadhaar.number.not_found",
                    "pan.number.not_found",
                    "validate.did_not_run",
                }
            )
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
