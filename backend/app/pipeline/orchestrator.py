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
from app.pipeline.stages import forensics as forensics_stage
from app.pipeline.stages import ocr as ocr_stage
from app.pipeline.stages.extract import extract_fields
from app.risk.engine import assess_case, assess_document
from app.rules.registry import validate_for_type
from app.schemas.document import (
    DocumentAnalysis,
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
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("file is not a decodable image")
        return img.shape[:2]

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
            doc_type, conf, cls_signals = result
            analysis.document_type = doc_type
            analysis.type_confidence = conf
            signals.extend(cls_signals)

    # --- extract ---
    fields_result, err = _timed(
        lambda: extract_fields(text, analysis.document_type), timings, "extract"
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

    analysis.signals = signals
    analysis.processing_ms = timings
    analysis.risk = assess_document(signals, EXPECTED_STAGES)
    return analysis


def verify_case(
    documents: list[tuple[bytes, str]],
    *,
    ocr_provider: ocr_stage.OCRProvider | None = None,
    enable_copy_move: bool = False,
) -> VerificationResult:
    """
    Run a full case: several documents assessed individually, then compared.

    Cross-document comparison is where a case becomes more than the sum of its
    documents -- two individually flawless documents that disagree about a date
    of birth are a finding that neither one produces alone.
    """
    from app.pipeline.stages.cross_document import compare_documents

    result = VerificationResult(case_id=str(uuid.uuid4()))

    for image_bytes, filename in documents:
        result.documents.append(
            analyze_document(
                image_bytes,
                filename,
                ocr_provider=ocr_provider,
                enable_copy_move=enable_copy_move,
            )
        )

    result.cross_document_signals = compare_documents(result.documents)
    result.overall_risk = assess_case(
        [d.risk for d in result.documents if d.risk],
        result.cross_document_signals,
    )
    result.completed_at = datetime.now(timezone.utc)
    return result
