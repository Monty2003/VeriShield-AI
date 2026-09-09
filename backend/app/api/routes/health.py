"""
Health and capability reporting.

/health reports which components are actually available, because this system
degrades in specific, meaningful ways. A deployment with no face model still
verifies documents -- but it cannot tell whether the presenter is the holder,
and the operator needs to know that WITHOUT having to infer it from
unexpectedly thin results.

Every degradation below names what stops working, not just what is missing.
"Face recognition unavailable" is a fact about the deployment; "identity of the
presenter cannot be verified" is what it means for the answers this service
gives, and that is what an operator has to act on.
"""

from __future__ import annotations

import importlib.util

from fastapi import APIRouter

from app.core.config import settings
from app.core.imaging import HEIF_AVAILABLE
from app.rules.registry import supported_types

router = APIRouter()


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


@router.get("/health")
def health() -> dict[str, object]:
    """Report liveness plus the real capability set of this deployment."""
    from app.storage.audit import audit_store, object_store

    ocr = _installed("paddleocr")
    face = _installed("insightface")
    torch = _installed("torch")

    # Deliberately NOT importing torch to answer this.
    #
    # /health is polled, and importing torch costs hundreds of megabytes of
    # resident memory for a boolean. Reporting "installed" without loading it
    # is the honest answer to what a health check is actually asking: whether
    # the capability is present, not whether a device is currently free.
    gpu = "unknown (torch not loaded)" if torch else False

    degraded: list[str] = []
    if not ocr:
        degraded.append(
            "No OCR engine: no text can be read, so document type, content rules "
            "and cross-document comparison cannot run. Image-level checks still "
            "work. Install with: pip install -r requirements-ml.txt"
        )
    if not face:
        degraded.append(
            "No face recognition: portraits can be located but not identified, so "
            "whether the presenter is the document holder CANNOT be verified. "
            "Install with: pip install insightface onnxruntime"
        )
    if not HEIF_AVAILABLE:
        degraded.append(
            "No HEIF decoder: photographs from most current phones cannot be read "
            "at all. Install with: pip install pillow-heif"
        )
    if torch and not gpu:
        degraded.append(
            "PyTorch is installed but no CUDA device is visible; models run on "
            "CPU, several times slower."
        )
    if not audit_store.available:
        degraded.append(
            "Audit store unreachable: verifications still run, but no record is "
            "kept, so a decision cannot be reproduced or justified afterwards. "
            f"({audit_store.error[:80]})"
        )
    if not object_store.available:
        degraded.append(
            "Object store unreachable: submitted documents are not retained, so a "
            "reviewer cannot re-examine the image behind a decision. "
            f"({object_store.error[:80]})"
        )

    return {
        "status": "ok",
        "service": settings.app_name,
        "capabilities": {
            "image_decoding": True,
            "heic": HEIF_AVAILABLE,
            "ocr": ocr,
            "rule_validation": True,
            "image_forensics": True,
            "face_detection": face or True,  # OpenCV fallback always detects
            "face_recognition": face,
            # Challenge-response only. There is no passive spoof classifier,
            # and its absence is a deliberate choice rather than a gap --
            # see app/pipeline/stages/liveness.py.
            "liveness_challenge_response": face,
            "liveness_passive_classifier": False,
            "authority_registry": "synthetic",
            "audit_trail": audit_store.available,
            "object_storage": object_store.available,
            "gpu": gpu,
        },
        "document_types_with_rulebook": supported_types(),
        "layers": {
            "L1_ingest": "ready",
            "L2_classify": "ready",
            "L3_ocr_extract": "ready" if ocr else "degraded",
            "L4_validate": "ready",
            # Implemented, measured, and deliberately off. See docs/FORENSICS.md:
            # calibration put both detectors at chance, so they contribute
            # nothing and say so rather than reporting noise as evidence.
            "L5_forensics": "metadata only (detectors disabled after calibration)",
            "L6_face": (
                "ready, with challenge-response liveness"
                if face
                else "detection only"
            ),
            "L7_cross_document": "ready",
            "L8_registry": "synthetic (non-authoritative)",
        },
        "degraded": degraded,
    }
