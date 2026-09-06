"""
Health and capability reporting.

/health reports which components are actually available, because this system
degrades in specific, meaningful ways. A deployment with no OCR engine still
verifies documents -- but only from image-level evidence -- and the operator
needs to know that WITHOUT having to infer it from unexpectedly thin results.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.rules.registry import supported_types

router = APIRouter()


def _probe(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


@router.get("/health")
def health() -> dict[str, object]:
    """Report liveness plus the real capability set of this deployment."""
    ocr_available = _probe("paddleocr")
    torch_available = _probe("torch")
    face_available = _probe("insightface")

    gpu = False
    if torch_available:
        try:
            import torch

            gpu = torch.cuda.is_available()
        except Exception:  # noqa: BLE001 -- a broken torch must not fail /health
            gpu = False

    degraded: list[str] = []
    if not ocr_available:
        degraded.append(
            "OCR engine not installed -- no text can be read, so content rules, "
            "MRZ checks and cross-document comparison cannot run. Image forensics "
            "still work. Install with: pip install -r requirements-ml.txt"
        )
    if not face_available:
        degraded.append(
            "Face recognition not installed -- portrait-to-selfie matching is "
            "unavailable."
        )
    if torch_available and not gpu:
        degraded.append(
            "PyTorch is installed but no CUDA device is visible; models will run "
            "on CPU, which is several times slower."
        )

    return {
        "status": "ok",
        "service": settings.app_name,
        "capabilities": {
            "ocr": ocr_available,
            "torch": torch_available,
            "gpu": gpu,
            "face_recognition": face_available,
            "image_forensics": True,  # OpenCV-only, always available
            "rule_validation": True,
        },
        "document_types_with_rulebook": supported_types(),
        "degraded": degraded,
    }
