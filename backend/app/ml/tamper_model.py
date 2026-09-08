"""
Learned tampering detector -- inference side.

Loads the patch classifier trained by scripts/train_tamper_detector.py and
slides it over a document to produce a suspicion heatmap and the regions that
cross its threshold.

Gated on the measurement, not on availability
---------------------------------------------
The saved checkpoint carries the verdict the training run reached on held-out
documents. If that verdict is not "usable", this module refuses to contribute
signals even when the weights load perfectly -- exactly as ELA and copy-move
are gated by their own calibration.

The reason is the same in all three cases. A detector that cannot separate
tampered from clean still emits confident-looking numbers, and a number
presented as evidence is acted on. Shipping one because it exists, rather than
because it works, is how a verification system starts making accusations it
cannot support.

The threshold is likewise the one measured at training time, not a value
chosen here. A threshold picked to make the demo look good is not a threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.schemas.signals import (
    Region,
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)

MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "models"
WEIGHTS = MODEL_DIR / "tamper_patch_resnet18.pt"
METRICS = MODEL_DIR / "tamper_metrics.json"

# Stride as a fraction of the patch. Half-overlap: fine enough to localise a
# text line, coarse enough that a 1600px document is a few hundred patches
# rather than a few hundred thousand.
STRIDE_FRACTION = 0.5

# A region must contain at least this many consecutive suspicious patches
# before it is reported. Single hot patches are what a detector produces on
# glare, a staple hole or a signature, and reporting them individually buries
# any real finding in noise.
MIN_PATCHES_PER_REGION = 3


@dataclass
class TamperPrediction:
    heatmap: np.ndarray          # per-patch probability grid
    regions: list[Region]
    peak_score: float
    available: bool = True
    verdict: str = ""
    note: str = ""


class TamperDetector:
    """Lazily-loaded patch classifier."""

    def __init__(self) -> None:
        self._model = None
        self._meta: dict = {}
        self._error = ""
        self._loaded = False

    # ---- availability ---------------------------------------------------

    @property
    def metrics(self) -> dict:
        if not self._meta and METRICS.exists():
            try:
                self._meta = json.loads(METRICS.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._meta = {}
        return self._meta

    @property
    def trained(self) -> bool:
        return WEIGHTS.exists()

    @property
    def usable(self) -> bool:
        """
        Whether the model earned the right to contribute to the RISK SCORE.

        Availability and usefulness are different questions, and only the
        second one matters here. The model currently trained scores
        "review_aid", not "usable" -- see `review_aid` below for why.
        """
        return self.trained and self.metrics.get("verdict") == "usable"

    @property
    def review_aid(self) -> bool:
        """
        Whether the model may be shown to a reviewer without scoring anything.

        The distinction earned its place from measurement. At the patch level
        the trained model is genuinely good (AUC 0.80 on held-out documents,
        against ~0.50 for the classical detectors). Slid across a whole
        document, it is not: sweeping every threshold and cluster size, the
        true and false positive rates move together, and the best point flags
        39% of tampered documents while also flagging 22% of genuine ones.

        That is the multiple-comparisons problem, not a broken model. A 10%
        per-patch false-positive rate is unremarkable for one patch and
        overwhelming across the two thousand a document contains.

        So it must never screen documents -- one genuine document in five
        would be flagged. But WHEN it fires it lands on the actual edit 50% of
        the time, against 2-8% for the classical detectors, so it is worth
        offering to a reviewer who is already looking. Shown on request,
        contributing nothing to the score.
        """
        return self.trained and self.metrics.get("verdict") in ("usable", "review_aid")

    def _load(self) -> bool:
        if self._loaded:
            return self._model is not None
        self._loaded = True

        if not self.trained:
            self._error = (
                "No trained tampering model. Train one with: "
                "python scripts/train_tamper_detector.py"
            )
            return False

        try:
            import torch
            import torch.nn as nn
            from torchvision.models import resnet18

            checkpoint = torch.load(WEIGHTS, map_location="cpu", weights_only=False)
            model = resnet18(weights=None)
            model.fc = nn.Linear(model.fc.in_features, 1)
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()

            self._model = model
            self._meta = {**self.metrics, **{
                k: checkpoint[k] for k in ("patch_size", "threshold", "auc", "verdict")
                if k in checkpoint
            }}
        except Exception as exc:  # noqa: BLE001 -- a broken model must not crash a verification
            self._error = f"{type(exc).__name__}: {exc}"
            self._model = None
            return False
        return True

    # ---- inference ------------------------------------------------------

    def predict(self, image_bgr: np.ndarray) -> TamperPrediction:
        """Slide the classifier over an image and return a suspicion map."""
        if not self.review_aid:
            verdict = self.metrics.get("verdict", "untrained")
            return TamperPrediction(
                heatmap=np.zeros((1, 1)),
                regions=[],
                peak_score=0.0,
                available=False,
                verdict=verdict,
                note=(
                    self._error
                    or f"The trained model scored '{verdict}' on held-out documents, "
                    f"so it is not used. Retraining on more source documents is the "
                    f"fix; lowering its threshold would only make it confident."
                ),
            )

        if not self._load():
            return TamperPrediction(
                heatmap=np.zeros((1, 1)),
                regions=[],
                peak_score=0.0,
                available=False,
                note=self._error,
            )

        import cv2
        import torch

        patch = int(self._meta.get("patch_size", 32))
        network_input = 64
        stride = max(1, int(patch * STRIDE_FRACTION))

        h, w = image_bgr.shape[:2]
        if h < patch or w < patch:
            return TamperPrediction(
                heatmap=np.zeros((1, 1)),
                regions=[],
                peak_score=0.0,
                available=False,
                note="Image is smaller than one patch.",
            )

        windows: list[np.ndarray] = []
        positions: list[tuple[int, int]] = []
        for y in range(0, h - patch + 1, stride):
            for x in range(0, w - patch + 1, stride):
                windows.append(
                    cv2.resize(
                        image_bgr[y : y + patch, x : x + patch],
                        (network_input, network_input),
                        interpolation=cv2.INTER_CUBIC,
                    )
                )
                positions.append((y, x))

        batch = torch.from_numpy(
            np.stack(windows).astype(np.float32) / 255.0
        ).permute(0, 3, 1, 2)

        probabilities: list[np.ndarray] = []
        with torch.no_grad():
            for i in range(0, len(batch), 512):
                probabilities.append(
                    torch.sigmoid(self._model(batch[i : i + 512]).squeeze(1)).numpy()
                )
        scores = np.concatenate(probabilities)

        rows = len(range(0, h - patch + 1, stride))
        cols = len(range(0, w - patch + 1, stride))
        heatmap = scores.reshape(rows, cols)

        threshold = float(self._meta.get("threshold") or 0.5)
        regions = self._regions_from(heatmap, threshold, patch, stride)

        return TamperPrediction(
            heatmap=heatmap,
            regions=regions,
            peak_score=float(scores.max()),
            available=True,
            verdict=self._meta.get("verdict", ""),
        )

    def _regions_from(
        self, heatmap: np.ndarray, threshold: float, patch: int, stride: int
    ) -> list[Region]:
        """Merge suspicious patches into regions a reviewer can be pointed at."""
        import cv2

        mask = (heatmap >= threshold).astype(np.uint8)
        if mask.sum() < MIN_PATCHES_PER_REGION:
            return []

        count, labels = cv2.connectedComponents(mask, connectivity=8)
        regions: list[Region] = []
        for component in range(1, count):
            ys, xs = np.where(labels == component)
            if len(ys) < MIN_PATCHES_PER_REGION:
                continue
            regions.append(
                Region(
                    x=int(xs.min() * stride),
                    y=int(ys.min() * stride),
                    width=int((xs.max() - xs.min()) * stride + patch),
                    height=int((ys.max() - ys.min()) * stride + patch),
                    label="learned tampering signal",
                    suspicion=float(heatmap[ys, xs].mean()),
                )
            )

        regions.sort(key=lambda r: r.suspicion or 0, reverse=True)
        return regions[:6]


_detector = TamperDetector()


def tamper_signals(image_bytes: bytes) -> list[Signal]:
    """Run the learned detector and express the result as Signals."""
    if not _detector.trained:
        return [
            signal(
                code="forensics.learned.untrained",
                stage=Stage.FORENSICS,
                title="Learned tampering detection",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "No tampering model has been trained for this deployment, so "
                    "no learned forensic check ran."
                ),
            )
        ]

    if not _detector.usable:
        metrics = _detector.metrics
        image_level = metrics.get("image_level", {})
        return [
            signal(
                code="forensics.learned.not_for_screening",
                stage=Stage.FORENSICS,
                title="Learned tampering detection",
                # SKIP, contributing zero risk. The model is real and is
                # available to a reviewer on request, but it does not screen.
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    f"A trained tampering model is available but does not screen "
                    f"documents. It is strong at the patch level (AUC "
                    f"{metrics.get('patch_auc', 0):.2f} on documents it never saw) "
                    f"and weak across a whole one: at its best setting it flags "
                    f"{image_level.get('tpr', 0):.0%} of tampered documents and "
                    f"also {image_level.get('fpr', 0):.0%} of genuine ones, so "
                    f"using it here would flag roughly one real document in five. "
                    f"It contributes nothing to this score. Where it does help is "
                    f"pointing at an edit once one is suspected -- it lands on the "
                    f"right region "
                    f"{metrics.get('localisation_when_fired', 0):.0%} of the time "
                    f"-- so it is offered to reviewers as an overlay instead."
                ),
                evidence={
                    "verdict": metrics.get("verdict"),
                    "patch_auc": metrics.get("patch_auc"),
                    "image_level": image_level,
                    "available_as": "reviewer overlay, on request",
                },
            )
        ]

    from app.core.imaging import decode_image

    try:
        prediction = _detector.predict(decode_image(image_bytes))
    except Exception as exc:  # noqa: BLE001
        return [
            signal(
                code="forensics.learned.error",
                stage=Stage.FORENSICS,
                title="Learned tampering detection",
                status=SignalStatus.ERROR,
                severity=Severity.LOW,
                reason=f"The learned tampering detector failed to run: {exc}",
            )
        ]

    if not prediction.available:
        return [
            signal(
                code="forensics.learned.unavailable",
                stage=Stage.FORENSICS,
                title="Learned tampering detection",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=prediction.note,
            )
        ]

    metrics = _detector.metrics
    if not prediction.regions:
        return [
            signal(
                code="forensics.learned.clean",
                stage=Stage.FORENSICS,
                title="Learned tampering detection",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"The learned detector found no region resembling an edit "
                    f"(peak {prediction.peak_score:.2f}, threshold "
                    f"{metrics.get('threshold', 0.5):.2f})."
                ),
                evidence={"peak_score": round(prediction.peak_score, 3)},
            )
        ]

    top = prediction.regions[0].suspicion or 0.0
    return [
        signal(
            code="forensics.learned.suspicious",
            stage=Stage.FORENSICS,
            title="Learned tampering detection",
            status=SignalStatus.WARN,
            severity=Severity.MEDIUM,
            # Confidence is capped by the model's MEASURED true-positive rate.
            # A model that finds half of all edits should not report any single
            # finding at 90% confidence, however high its own output goes.
            confidence=min(0.8, float(metrics.get("tpr") or 0.5)),
            reason=(
                f"The learned detector flagged {len(prediction.regions)} region(s) "
                f"as resembling an edit (peak suspicion {top:.0%}). This model was "
                f"trained on synthetically tampered documents and measured at "
                f"{float(metrics.get('tpr') or 0):.0%} detection with "
                f"{float(metrics.get('fpr') or 0):.0%} false positives on documents "
                f"it had not seen -- so treat it as a place to look, not a finding."
            ),
            evidence={
                "regions": len(prediction.regions),
                "peak_score": round(prediction.peak_score, 3),
                "model_auc": metrics.get("auc"),
                "model_tpr": metrics.get("tpr"),
                "model_fpr": metrics.get("fpr"),
            },
            regions=prediction.regions,
        )
    ]
