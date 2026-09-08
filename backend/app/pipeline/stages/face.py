"""
Face detection and verification (Layer 6).

Answers a question no other layer can: is the person presenting this document
the person it was issued to? Everything upstream reasons about the artefact;
this reasons about the holder.

Two separable capabilities, and the distinction matters when the stack is only
partly installed:

  * **Detection** -- locate the portrait on the document. Available from
    OpenCV alone, and useful on its own: it tells the forensic layer where the
    photograph is, and the UI where to draw it.
  * **Recognition** -- decide whether two faces are the same person. Needs a
    real embedding model (ArcFace via insightface). Without it the honest
    answer is that identity was not checked, and this module says exactly that
    rather than falling back to something weaker and calling it a match.

A pixel comparison, a histogram, or a Haar-cascade similarity would all produce
a number here. None of them measures identity, and a number that looks like a
match score but is not one is worse than an empty field: it will be read as
evidence.

On thresholds
-------------
Face similarity is the one place in this system where the threshold is
genuinely a policy decision rather than an arithmetic fact. The values below
follow common ArcFace practice, and they are stated as constants with their
rationale so they can be argued with -- not buried in a comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from app.core.imaging import decode_image
from app.schemas.signals import (
    Region,
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)

# ArcFace cosine similarity thresholds.
#
# 0.36 is the conventional operating point for ArcFace embeddings at a low
# false-accept rate, and identity verification wants a low false-accept rate:
# wrongly matching two people is the failure that lets someone use another
# person's document.
#
# Between the two values sits a band where the evidence genuinely does not
# decide. That band routes to a human rather than being forced into a verdict,
# which is the whole reason it exists.
STRONG_MATCH_THRESHOLD = 0.45
POSSIBLE_MATCH_THRESHOLD = 0.28

# A face smaller than this on the document is too low-resolution for an
# embedding to mean much, whatever number comes back.
MIN_FACE_PIXELS = 60


@dataclass
class DetectedFace:
    """One detected face, with its embedding when a recognition model ran."""

    region: Region
    confidence: float
    embedding: np.ndarray | None = None

    @property
    def has_embedding(self) -> bool:
        return self.embedding is not None


@dataclass
class FaceResult:
    faces: list[DetectedFace] = field(default_factory=list)
    engine: str = "none"
    detection_available: bool = False
    recognition_available: bool = False
    error: str = ""

    @property
    def primary(self) -> DetectedFace | None:
        """The largest face -- the document portrait, not a face in a watermark."""
        if not self.faces:
            return None
        return max(self.faces, key=lambda f: f.region.width * f.region.height)


class FaceProvider(Protocol):
    name: str
    detection_available: bool
    recognition_available: bool

    def analyze(self, image_bytes: bytes) -> FaceResult: ...


class NullFaceProvider:
    """No face capability at all."""

    name = "none"
    detection_available = False
    recognition_available = False

    def analyze(self, image_bytes: bytes) -> FaceResult:
        return FaceResult(
            engine="none",
            error=(
                "No face model is installed. Install with: "
                "pip install insightface onnxruntime"
            ),
        )


class OpenCVFaceProvider:
    """
    Detection only, using the Haar cascade bundled with OpenCV.

    Deliberately reports recognition_available = False. It can find a portrait,
    which is genuinely useful; it cannot tell one person from another, and it
    does not pretend to. The alternative -- comparing crops by pixel distance
    and calling the result a similarity -- would produce a plausible-looking
    number that measures lighting and pose rather than identity.
    """

    name = "opencv-haar"
    detection_available = True
    recognition_available = False

    def analyze(self, image_bytes: bytes) -> FaceResult:
        import cv2

        try:
            img = decode_image(image_bytes)
        except Exception as exc:  # noqa: BLE001
            return FaceResult(engine=self.name, error=str(exc))

        cascade = cv2.CascadeClassifier(
            str(cv2.data.haarcascades) + "haarcascade_frontalface_default.xml"
        )
        if cascade.empty():
            return FaceResult(engine=self.name, error="Haar cascade unavailable")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        detections = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(MIN_FACE_PIXELS, MIN_FACE_PIXELS)
        )

        faces = [
            DetectedFace(
                region=Region(x=int(x), y=int(y), width=int(w), height=int(h), label="face"),
                # Haar returns no score, so this records "detected" rather than
                # inventing a confidence the detector never produced.
                confidence=0.5,
            )
            for x, y, w, h in detections
        ]
        return FaceResult(
            faces=faces,
            engine=self.name,
            detection_available=True,
            recognition_available=False,
        )


class InsightFaceProvider:
    """
    RetinaFace detection plus ArcFace embeddings.

    Loaded lazily and kept as a single instance: on the 6 GB target GPU this
    model must not be resident alongside OCR.
    """

    name = "insightface"
    detection_available = True
    recognition_available = True

    def __init__(self, use_gpu: bool = False) -> None:
        self.use_gpu = use_gpu
        self._app = None

    def _get_app(self):
        if self._app is None:
            from insightface.app import FaceAnalysis  # noqa: PLC0415

            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if self.use_gpu
                else ["CPUExecutionProvider"]
            )
            app = FaceAnalysis(name="buffalo_l", providers=providers)
            app.prepare(ctx_id=0 if self.use_gpu else -1, det_size=(640, 640))
            self._app = app
        return self._app

    def analyze(self, image_bytes: bytes) -> FaceResult:
        try:
            app = self._get_app()
            img = decode_image(image_bytes)
            detections = app.get(img)
        except ImportError as exc:
            return FaceResult(engine=self.name, error=f"insightface not installed: {exc}")
        except Exception as exc:  # noqa: BLE001 -- surface engine failure as a signal
            return FaceResult(engine=self.name, error=str(exc))

        faces = []
        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            faces.append(
                DetectedFace(
                    region=Region(
                        x=x1, y=y1, width=x2 - x1, height=y2 - y1, label="face"
                    ),
                    confidence=float(getattr(det, "det_score", 0.0)),
                    embedding=getattr(det, "normed_embedding", None),
                )
            )

        return FaceResult(
            faces=faces,
            engine=self.name,
            detection_available=True,
            recognition_available=True,
        )


# One provider per process, keyed by device.
#
# The provider itself is cheap to construct; the five ONNX models behind it are
# not. Returning a fresh instance each call meant every caller that did not
# hold onto one reloaded the whole model set -- measured at roughly 23 seconds
# per liveness frame against 0.4 seconds once the models are resident, which
# read as "the pipeline is slow" rather than "the models are being reloaded".
_PROVIDER_CACHE: dict[bool, FaceProvider] = {}


def get_default_provider(use_gpu: bool = False) -> FaceProvider:
    """
    Best available provider: full recognition, else detection, else nothing.

    Cached per process. Callers may still pass their own provider; this is the
    default for those that do not.
    """
    import importlib.util

    cached = _PROVIDER_CACHE.get(use_gpu)
    if cached is not None:
        return cached

    if importlib.util.find_spec("insightface") is not None:
        provider: FaceProvider = InsightFaceProvider(use_gpu=use_gpu)
    elif importlib.util.find_spec("cv2") is not None:
        provider = OpenCVFaceProvider()
    else:
        provider = NullFaceProvider()

    _PROVIDER_CACHE[use_gpu] = provider
    return provider


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two normalised embeddings."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def detect_document_face(image_bytes: bytes, provider: FaceProvider | None = None):
    """Locate the portrait on a document. Returns (FaceResult, signals)."""
    provider = provider or get_default_provider()
    result = provider.analyze(image_bytes)

    if result.error:
        return result, [
            signal(
                code="face.detection.error",
                stage=Stage.FACE,
                title="Portrait detection",
                status=SignalStatus.ERROR,
                severity=Severity.LOW,
                reason=f"Face detection could not run: {result.error}",
                evidence={"engine": result.engine},
            )
        ]

    if not result.faces:
        return result, [
            signal(
                code="face.detection.none",
                stage=Stage.FACE,
                title="Portrait detection",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "No portrait was found on this document. Many document types "
                    "and every reverse side legitimately carry none, so this is "
                    "not itself a finding."
                ),
                evidence={"engine": result.engine},
            )
        ]

    primary = result.primary
    small = min(primary.region.width, primary.region.height) < MIN_FACE_PIXELS

    return result, [
        signal(
            code="face.detection.found",
            stage=Stage.FACE,
            title="Portrait detection",
            status=SignalStatus.WARN if small else SignalStatus.PASS,
            severity=Severity.LOW if small else Severity.INFO,
            reason=(
                f"Found {len(result.faces)} face(s); the portrait measures "
                f"{primary.region.width}x{primary.region.height} pixels."
                + (
                    " That is small enough that any identity comparison against it "
                    "would be unreliable."
                    if small
                    else ""
                )
            ),
            evidence={"faces": len(result.faces), "engine": result.engine},
            regions=[primary.region],
        )
    ]


def verify_faces(
    document_image: bytes,
    selfie_image: bytes,
    provider: FaceProvider | None = None,
) -> list[Signal]:
    """
    Compare the portrait on a document against a presented face.

    Three outcomes rather than two. The middle one -- similarity in the band
    where the evidence does not decide -- routes to a human instead of being
    forced into a verdict. Faces vary with age, lighting, pose and expression,
    and a system that always answers yes or no will be confidently wrong about
    real people at both ends.
    """
    provider = provider or get_default_provider()

    if not provider.recognition_available:
        return [
            signal(
                code="face.recognition.unavailable",
                stage=Stage.FACE,
                title="Face verification",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                # Blocking: the holder's identity is the thing a selfie was
                # submitted to establish, and it was not established.
                blocking=True,
                reason=(
                    f"A face was submitted for comparison, but this deployment has "
                    f"no face-recognition model ({provider.name} can detect faces "
                    f"but not identify them). Whether the presenter is the document "
                    f"holder was NOT checked. Install with: pip install insightface "
                    f"onnxruntime"
                ),
                evidence={"engine": provider.name},
            )
        ]

    doc_result = provider.analyze(document_image)
    selfie_result = provider.analyze(selfie_image)

    for label, result in (("document", doc_result), ("presented face", selfie_result)):
        if result.error:
            return [
                signal(
                    code="face.compare.error",
                    stage=Stage.FACE,
                    title="Face verification",
                    status=SignalStatus.ERROR,
                    severity=Severity.HIGH,
                    blocking=True,
                    reason=f"Face analysis failed on the {label}: {result.error}",
                )
            ]
        if not result.faces:
            return [
                signal(
                    code="face.compare.missing",
                    stage=Stage.FACE,
                    title="Face verification",
                    status=SignalStatus.ERROR,
                    severity=Severity.HIGH,
                    blocking=True,
                    reason=(
                        f"No face was found in the {label}, so the two could not be "
                        f"compared and the holder's identity was not verified."
                    ),
                )
            ]

    doc_face, selfie_face = doc_result.primary, selfie_result.primary
    if not (doc_face.has_embedding and selfie_face.has_embedding):
        return [
            signal(
                code="face.compare.no_embedding",
                stage=Stage.FACE,
                title="Face verification",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    "Faces were located but no identity embedding was produced, so "
                    "they could not be compared."
                ),
            )
        ]

    similarity = cosine_similarity(doc_face.embedding, selfie_face.embedding)
    small = min(doc_face.region.width, doc_face.region.height) < MIN_FACE_PIXELS

    if similarity >= STRONG_MATCH_THRESHOLD:
        return [
            signal(
                code="face.match.strong",
                stage=Stage.FACE,
                title="Face verification",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                confidence=0.6 if small else 0.9,
                reason=(
                    f"The presented face matches the document portrait "
                    f"(similarity {similarity:.2f}, above the {STRONG_MATCH_THRESHOLD} "
                    f"threshold)."
                    + (
                        " The document portrait is small, so treat this as weaker "
                        "than the number suggests."
                        if small
                        else ""
                    )
                ),
                evidence={"similarity": round(similarity, 4)},
                regions=[doc_face.region],
            )
        ]

    if similarity >= POSSIBLE_MATCH_THRESHOLD:
        return [
            signal(
                code="face.match.uncertain",
                stage=Stage.FACE,
                title="Face verification",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                confidence=0.5,
                blocking=True,
                reason=(
                    f"Similarity between the presented face and the document "
                    f"portrait is {similarity:.2f} -- between the {POSSIBLE_MATCH_THRESHOLD} "
                    f"and {STRONG_MATCH_THRESHOLD} thresholds, where the comparison "
                    f"does not decide. Age difference, lighting and pose all produce "
                    f"scores in this range for the same person, and so do some "
                    f"genuinely different people. A human should look."
                ),
                evidence={"similarity": round(similarity, 4)},
                regions=[doc_face.region],
            )
        ]

    return [
        signal(
            code="face.match.mismatch",
            stage=Stage.FACE,
            title="Face verification",
            status=SignalStatus.FAIL,
            severity=Severity.CRITICAL,
            confidence=0.85,
            reason=(
                f"The presented face does not match the document portrait "
                f"(similarity {similarity:.2f}, below {POSSIBLE_MATCH_THRESHOLD}). "
                f"The document may be genuine, but the person presenting it does "
                f"not appear to be its holder."
            ),
            evidence={"similarity": round(similarity, 4)},
            regions=[doc_face.region],
        )
    ]
