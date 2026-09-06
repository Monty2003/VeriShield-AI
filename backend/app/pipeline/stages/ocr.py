"""
OCR and text extraction (Layer 3).

PaddleOCR is the intended engine, but it is a large install and lives in
requirements-ml.txt. So OCR sits behind a provider interface with three
implementations:

  PaddleOCRProvider -- the real one, lazily imported so its absence costs
                       nothing until it is actually wanted.
  InjectedTextProvider -- accepts MRZ/text supplied by the caller. This is
                       how the rule and risk layers are tested and demoed
                       without a 2 GB download, and how a reviewer can
                       correct a misread field and re-run the assessment.
  NullProvider      -- reports honestly that OCR is unavailable.

The Null case emits ERROR rather than silently returning empty text. Empty
text would flow downstream as "no MRZ found", which reads as a property of
the DOCUMENT when it is really a property of our deployment -- exactly the
kind of confusion that produces a wrong decision about a real person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.signals import (
    Region,
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)


@dataclass
class TextLine:
    """One recognised line with its box and confidence."""

    text: str
    confidence: float
    region: Region | None = None


@dataclass
class OCRResult:
    lines: list[TextLine] = field(default_factory=list)
    available: bool = True
    engine: str = "none"
    error: str = ""

    @property
    def full_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)


class OCRProvider(Protocol):
    name: str

    def read(self, image_bytes: bytes) -> OCRResult: ...


class NullProvider:
    """Used when no OCR engine is installed."""

    name = "none"

    def read(self, image_bytes: bytes) -> OCRResult:
        return OCRResult(
            available=False,
            engine="none",
            error=(
                "No OCR engine is installed. Install the model dependencies "
                "with: pip install -r requirements-ml.txt"
            ),
        )


class InjectedTextProvider:
    """
    Returns caller-supplied text instead of reading the image.

    Two real uses beyond testing: demonstrating the rule and risk layers
    before the ML stack is installed, and letting a human reviewer correct an
    OCR misread and re-run the assessment against the corrected value -- which
    is a genuine requirement, since a misread MRZ character produces a failed
    checksum that looks exactly like tampering.
    """

    name = "injected"

    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, image_bytes: bytes) -> OCRResult:
        lines = [
            TextLine(text=ln, confidence=1.0)
            for ln in self.text.splitlines()
            if ln.strip()
        ]
        return OCRResult(lines=lines, available=True, engine="injected")


class PaddleOCRProvider:
    """
    PaddleOCR wrapper.

    Imported lazily and constructed once. On the 6 GB target GPU this model
    must not be held resident alongside the others, so callers should release
    it via the model manager when switching stages.
    """

    name = "paddleocr"

    def __init__(self, lang: str = "en", use_gpu: bool = True) -> None:
        self.lang = lang
        self.use_gpu = use_gpu
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from paddleocr import PaddleOCR  # noqa: PLC0415 -- lazy by design

            self._engine = PaddleOCR(
                use_angle_cls=True,
                lang=self.lang,
                use_gpu=self.use_gpu,
                show_log=False,
            )
        return self._engine

    def read(self, image_bytes: bytes) -> OCRResult:
        try:
            import numpy as np  # noqa: PLC0415
            import cv2  # noqa: PLC0415

            engine = self._get_engine()
            img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return OCRResult(available=False, engine=self.name, error="undecodable image")

            raw = engine.ocr(img, cls=True)
            lines: list[TextLine] = []
            for page in raw or []:
                for entry in page or []:
                    box, (text, conf) = entry
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    lines.append(
                        TextLine(
                            text=text,
                            confidence=float(conf),
                            region=Region(
                                x=int(min(xs)),
                                y=int(min(ys)),
                                width=int(max(xs) - min(xs)),
                                height=int(max(ys) - min(ys)),
                            ),
                        )
                    )
            return OCRResult(lines=lines, available=True, engine=self.name)

        except ImportError as exc:
            return OCRResult(
                available=False,
                engine=self.name,
                error=f"PaddleOCR is not installed: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 -- surface any engine failure as a signal
            return OCRResult(available=False, engine=self.name, error=str(exc))


def get_default_provider(lang: str = "en", use_gpu: bool = True) -> OCRProvider:
    """Return PaddleOCR if importable, otherwise the Null provider."""
    try:
        import paddleocr  # noqa: F401, PLC0415

        return PaddleOCRProvider(lang=lang, use_gpu=use_gpu)
    except ImportError:
        return NullProvider()


# Below this mean confidence, downstream findings are unsafe to act on: a
# failed checksum computed from characters we are unsure of is not evidence
# of tampering, it is evidence of a bad photograph.
LOW_OCR_CONFIDENCE = 0.65


def ocr_signals(result: OCRResult) -> list[Signal]:
    """Turn an OCR result into Signals describing how well we could read."""
    if not result.available:
        return [
            signal(
                code="ocr.unavailable",
                stage=Stage.OCR,
                title="Text extraction",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                reason=(
                    f"Text could not be extracted from this document: {result.error} "
                    f"Every downstream content check depends on OCR, so this "
                    f"assessment is based only on image-level evidence."
                ),
                evidence={"engine": result.engine, "error": result.error},
            )
        ]

    if not result.lines:
        return [
            signal(
                code="ocr.no_text",
                stage=Stage.OCR,
                title="Text extraction",
                status=SignalStatus.WARN,
                severity=Severity.HIGH,
                reason=(
                    "The OCR engine ran but found no readable text. The image is "
                    "likely blurred, badly lit, or not a document at all."
                ),
                evidence={"engine": result.engine},
            )
        ]

    mean_conf = result.mean_confidence
    if mean_conf < LOW_OCR_CONFIDENCE:
        return [
            signal(
                code="ocr.low_confidence",
                stage=Stage.OCR,
                title="Text extraction",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                confidence=0.8,
                reason=(
                    f"Text was extracted but at low average confidence "
                    f"({mean_conf:.0%} over {len(result.lines)} lines). Field and "
                    f"checksum results below may reflect misreads rather than "
                    f"problems with the document -- consider a clearer scan."
                ),
                evidence={
                    "engine": result.engine,
                    "lines": len(result.lines),
                    "mean_confidence": round(mean_conf, 3),
                },
            )
        ]

    return [
        signal(
            code="ocr.ok",
            stage=Stage.OCR,
            title="Text extraction",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            reason=(
                f"Read {len(result.lines)} lines of text at {mean_conf:.0%} average "
                f"confidence using {result.engine}."
            ),
            evidence={
                "engine": result.engine,
                "lines": len(result.lines),
                "mean_confidence": round(mean_conf, 3),
            },
        )
    ]
