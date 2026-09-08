"""
Portrait substitution detection.

Answers one narrow question: is the photograph on this card consistent with the
card around it, or was it put there afterwards?

Why this can work when general tampering detection did not
----------------------------------------------------------
The general detectors failed for a specific, measurable reason. Sliding a
classifier across a document evaluates roughly two thousand patches, so a 10%
per-patch false-positive rate flags a fifth of all genuine documents. That is
the multiple-comparisons problem, and no threshold escapes it.

Here there is exactly ONE region to test, and a face detector says precisely
where it is. One comparison, one decision -- so a per-test false-positive rate
of a few percent stays a few percent. The problem is not easier because the
signals are stronger; it is easier because we stopped asking it two thousand
times.

What is compared
----------------
A printed card is manufactured in one pass: the portrait is printed with the
same process, at the same resolution, in the same ink, as the text beside it.
A digitally inserted photograph is not, and disagrees on properties that
survive being photographed again:

  * **Sharpness.** Printed-then-photographed content shares the camera's focus
    and the print's dot pitch. Pasted content carries its own.
  * **Noise floor.** Different source, different sensor, different history.
  * **Colour temperature.** The card was lit once; a pasted photo was lit
    somewhere else.
  * **Boundary continuity.** Print bleeds into its surroundings at the pixel
    level. A digital paste has an edge that is too clean.

None of these is conclusive alone -- a genuine card photographed at an angle
has a blurrier corner, and a dark portrait shifts the colour statistics
honestly. They are combined, and the reason string names which ones disagreed
so a reviewer can judge rather than accept a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.schemas.signals import (
    Region,
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)

# A ring of card around the portrait, as a fraction of the portrait's size.
# The comparison is against the card IMMEDIATELY surrounding the photo rather
# than the whole document: lighting and focus vary across a card, so the fair
# comparison is local.
SURROUND_RATIO = 0.6

# How many standard deviations a property must differ by before it counts.
# Deliberately loose per-property, because the decision comes from how many
# agree rather than from any one crossing a line.
PROPERTY_SIGMA = 2.0

# How many of the four properties must disagree before this is reported.
# Two, not one: any single property differs on plenty of genuine cards --
# an angled photograph blurs one corner, a dark portrait shifts colour.
MIN_DISAGREEING_PROPERTIES = 2

# Below this the portrait is too small to measure anything reliably.
MIN_PORTRAIT_PIXELS = 60


@dataclass
class ConsistencyCheck:
    """One property compared between the portrait and the card around it."""

    name: str
    portrait_value: float
    surround_value: float
    ratio: float
    disagrees: bool
    note: str = ""


@dataclass
class SubstitutionResult:
    checks: list[ConsistencyCheck] = field(default_factory=list)
    applicable: bool = True
    note: str = ""

    @property
    def disagreeing(self) -> list[ConsistencyCheck]:
        return [c for c in self.checks if c.disagrees]

    @property
    def suspicious(self) -> bool:
        return len(self.disagreeing) >= MIN_DISAGREEING_PROPERTIES


def _sharpness(image: np.ndarray) -> float:
    """Variance of the Laplacian -- the standard focus measure."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _noise_floor(image: np.ndarray) -> float:
    """
    Residual after median filtering, in flat areas only.

    Restricting to flat areas matters for the same reason it did in the noise
    detector: measuring over text or a face returns the strength of the
    content, not of the noise.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    residual = gray.astype(np.float32) - cv2.medianBlur(gray, 3).astype(np.float32)

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    texture = cv2.magnitude(gx, gy)

    flat = texture <= np.median(texture)
    if flat.sum() < 32:
        return float(residual.std())
    return float(residual[flat].std())


def _colour_temperature(image: np.ndarray) -> float:
    """
    Blue-to-red channel ratio. A crude but robust white-balance proxy.

    Two photographs taken under different light disagree here even after both
    have been printed and rephotographed, because the difference is baked into
    the pixels before printing.
    """
    b, _, r = cv2.split(image.astype(np.float32))
    return float((b.mean() + 1.0) / (r.mean() + 1.0))


def _boundary_gradient(image: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """
    Edge strength along the portrait's border, relative to just inside it.

    Ink bleeds: a printed boundary is gradual at pixel level. A digital paste
    leaves an edge sharper than anything the printing process produces, so the
    ratio of border energy to interior energy runs high.
    """
    x, y, w, h = box
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy)

    band = 3
    h_img, w_img = gray.shape
    x0, y0 = max(0, x - band), max(0, y - band)
    x1, y1 = min(w_img, x + w + band), min(h_img, y + h + band)

    outer = magnitude[y0:y1, x0:x1]
    inner_x0, inner_y0 = max(0, x + band), max(0, y + band)
    inner_x1, inner_y1 = min(w_img, x + w - band), min(h_img, y + h - band)
    if inner_x1 <= inner_x0 or inner_y1 <= inner_y0:
        return 0.0

    inner = magnitude[inner_y0:inner_y1, inner_x0:inner_x1]
    ring_energy = (outer.sum() - inner.sum()) / max(outer.size - inner.size, 1)
    return float(ring_energy / (float(inner.mean()) + 1e-6))


def check_portrait_consistency(
    image: np.ndarray, portrait: Region
) -> SubstitutionResult:
    """Compare the portrait against the card immediately surrounding it."""
    h_img, w_img = image.shape[:2]
    x, y, w, h = portrait.x, portrait.y, portrait.width, portrait.height

    if min(w, h) < MIN_PORTRAIT_PIXELS:
        return SubstitutionResult(
            applicable=False,
            note=(
                f"The portrait is only {w}x{h} pixels -- too small to compare "
                f"reliably against the card around it."
            ),
        )

    pad_x, pad_y = int(w * SURROUND_RATIO), int(h * SURROUND_RATIO)
    ox0, oy0 = max(0, x - pad_x), max(0, y - pad_y)
    ox1, oy1 = min(w_img, x + w + pad_x), min(h_img, y + h + pad_y)

    inside = image[y : y + h, x : x + w]
    outer = image[oy0:oy1, ox0:ox1]

    if inside.size == 0 or outer.size == 0:
        return SubstitutionResult(applicable=False, note="Could not crop the regions.")

    # Mask the portrait out of the surrounding crop so the comparison is
    # against card only, not against a ring that still contains the photo.
    surround_mask = np.ones(outer.shape[:2], dtype=bool)
    surround_mask[y - oy0 : y - oy0 + h, x - ox0 : x - ox0 + w] = False
    if surround_mask.sum() < 500:
        return SubstitutionResult(
            applicable=False,
            note="Not enough card visible around the portrait to compare against.",
        )

    surround = outer.copy()
    surround[~surround_mask] = np.median(outer[surround_mask], axis=0).astype(np.uint8)

    checks: list[ConsistencyCheck] = []

    def compare(name: str, a: float, b: float, note: str) -> None:
        # Ratio rather than difference: these quantities have no shared scale,
        # and a ratio is what "twice as sharp" means.
        ratio = (a + 1e-6) / (b + 1e-6)
        # Symmetric: a portrait that is much sharper OR much blurrier than the
        # card is equally anomalous.
        deviation = max(ratio, 1 / ratio)
        checks.append(
            ConsistencyCheck(
                name=name,
                portrait_value=round(a, 4),
                surround_value=round(b, 4),
                ratio=round(ratio, 3),
                disagrees=deviation >= PROPERTY_SIGMA,
                note=note,
            )
        )

    compare(
        "sharpness",
        _sharpness(inside),
        _sharpness(surround),
        "printed content shares the camera's focus and the print's dot pitch",
    )
    compare(
        "noise floor",
        _noise_floor(inside),
        _noise_floor(surround),
        "one capture has one sensor-noise level throughout",
    )
    compare(
        "colour temperature",
        _colour_temperature(inside),
        _colour_temperature(surround),
        "the card was lit once; a pasted photo was lit elsewhere",
    )

    boundary = _boundary_gradient(image, (x, y, w, h))
    checks.append(
        ConsistencyCheck(
            name="boundary sharpness",
            portrait_value=round(boundary, 4),
            surround_value=1.0,
            ratio=round(boundary, 3),
            # Printing bleeds; a digital paste does not. A border much sharper
            # than the content inside it is what an inserted rectangle looks
            # like.
            disagrees=boundary >= 2.5,
            note="ink bleeds at a printed edge; a digital paste does not",
        )
    )

    return SubstitutionResult(checks=checks)


def portrait_substitution_signals(
    image: np.ndarray, portrait: Region | None
) -> list[Signal]:
    """Express the portrait consistency check as Signals."""
    if portrait is None:
        return [
            signal(
                code="forensics.portrait.no_face",
                stage=Stage.FORENSICS,
                title="Portrait substitution check",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "No portrait was located on this document, so there was "
                    "nothing to check for substitution. Many document types and "
                    "every reverse side carry no photograph."
                ),
            )
        ]

    result = check_portrait_consistency(image, portrait)

    if not result.applicable:
        return [
            signal(
                code="forensics.portrait.not_applicable",
                stage=Stage.FORENSICS,
                title="Portrait substitution check",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=result.note,
            )
        ]

    detail = "; ".join(
        f"{c.name} differs {c.ratio:.1f}x ({c.note})" for c in result.disagreeing
    )
    evidence = {
        "checks": [
            {
                "property": c.name,
                "portrait": c.portrait_value,
                "card": c.surround_value,
                "ratio": c.ratio,
                "disagrees": c.disagrees,
            }
            for c in result.checks
        ],
        "disagreeing": len(result.disagreeing),
    }

    if result.suspicious:
        return [
            signal(
                code="forensics.portrait.inconsistent",
                stage=Stage.FORENSICS,
                title="Portrait substitution check",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                # High, but not certain. Angled photographs, glare across a
                # laminate and very dark portraits all move these properties on
                # perfectly genuine cards.
                confidence=0.65,
                reason=(
                    f"The photograph on this card does not match the card around "
                    f"it on {len(result.disagreeing)} of "
                    f"{len(result.checks)} properties: {detail}. A card is printed "
                    f"in one pass, so its portrait should share the print and "
                    f"capture characteristics of the text beside it. This is what "
                    f"a substituted photograph looks like -- though an angled or "
                    f"badly lit capture can produce it too, so confirm visually."
                ),
                evidence=evidence,
                regions=[portrait],
            )
        ]

    return [
        signal(
            code="forensics.portrait.consistent",
            stage=Stage.FORENSICS,
            title="Portrait substitution check",
            status=SignalStatus.PASS,
            severity=Severity.INFO,
            confidence=0.7,
            reason=(
                f"The photograph is consistent with the card around it across "
                f"{len(result.checks) - len(result.disagreeing)} of "
                f"{len(result.checks)} properties (sharpness, noise, colour and "
                f"edge), which is what a portrait printed as part of the card "
                f"looks like."
            ),
            evidence=evidence,
            regions=[portrait],
        )
    ]
