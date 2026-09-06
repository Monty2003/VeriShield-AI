"""
Image forensics (Layer 5) -- classical, model-free signal extraction.

Everything here runs on OpenCV/NumPy alone. That is a deliberate architectural
choice, not a placeholder: classical forensics are deterministic, fast, need no
training data, and -- most importantly -- are EXPLAINABLE. When ELA highlights a
rectangle around a photograph, we can show the reviewer the actual residual
image that produced the finding. A Swin Transformer that outputs 0.87 cannot.

The learned detector (Layer 5, Phase 2) is intended to sit ALONGSIDE these as
another voice in the Risk Engine, not to replace them. Multi-signal forensics
degrade gracefully: if one method is fooled, the others still speak.

Known limits, stated honestly because they matter for how results are read:
  * ELA is only meaningful on JPEG input. On PNG or a re-encoded upload it is
    close to noise, so we report NOT APPLICABLE rather than a made-up number.
  * A scan or photograph of a genuine document legitimately shows compression
    and noise variation. These methods flag ANOMALY, not FORGERY, and the
    reasons we emit say so.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from app.schemas.signals import (
    Region,
    Severity,
    Signal,
    SignalStatus,
    Stage,
    signal,
)

# ELA recompression quality. 90 is the conventional choice: high enough that
# untouched areas produce almost no residual, low enough that edited areas --
# which have already been through a different compression history -- stand out.
ELA_QUALITY = 90

# Grid used to localise anomalies. 16x16 over a typical ID card gives cells
# roughly the size of a single field, which is the granularity a reviewer can
# actually act on ("the name line is suspicious", not "pixel 412,88").
GRID_ROWS = 16
GRID_COLS = 16

# A cell must exceed the image's own mean by this many standard deviations
# before we call it anomalous. Calibrating against the image itself rather
# than a fixed threshold is what keeps this usable across scanners, phone
# cameras and lighting conditions.
ELA_SIGMA_THRESHOLD = 2.5
NOISE_SIGMA_THRESHOLD = 2.5

# Absolute floor on the ELA residual, on a 0-765 scale (sum of |diff| across
# three channels). Recompressing an untouched image at the same quality moves
# pixels by at most a level or two, so anything below this is quantisation
# noise with no information in it.
#
# The floor exists because a relative test alone is unsafe on flat images: if
# almost every cell has a residual near zero, the standard deviation collapses
# and trivial variation scores as a large outlier. That is not a hypothetical
# -- without this, ELA flagged a blank synthetic document and scored it 22/100.
MIN_ELA_ABSOLUTE_RESIDUAL = 10.0

# Below this many anomalous cells we say nothing. Isolated hot cells are
# almost always specular highlights or holograms, not edits.
MIN_ANOMALOUS_CELLS = 3


@dataclass
class ForensicMap:
    """A per-cell suspicion map plus the regions that crossed the threshold."""

    heatmap: np.ndarray  # GRID_ROWS x GRID_COLS, normalised 0-1
    regions: list[Region]
    mean: float
    std: float
    applicable: bool = True
    note: str = ""


def _to_cv(image_bytes: bytes) -> np.ndarray:
    """Decode uploaded bytes to a BGR image array."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image")
    return img


def _cells_to_regions(
    heatmap: np.ndarray,
    threshold: float,
    img_w: int,
    img_h: int,
    label: str,
) -> list[Region]:
    """
    Convert hot grid cells into merged pixel-space regions.

    Adjacent hot cells are merged with connected components so the reviewer
    sees one box around a tampered photo, not forty confetti squares.
    """
    mask = (heatmap >= threshold).astype(np.uint8)
    if mask.sum() < MIN_ANOMALOUS_CELLS:
        return []

    n_labels, labels = cv2.connectedComponents(mask, connectivity=8)
    cell_h = img_h / GRID_ROWS
    cell_w = img_w / GRID_COLS

    regions: list[Region] = []
    for comp in range(1, n_labels):
        ys, xs = np.where(labels == comp)
        if len(ys) < 2:
            # A single isolated cell is not evidence; skip it.
            continue
        y0, y1 = ys.min(), ys.max() + 1
        x0, x1 = xs.min(), xs.max() + 1
        suspicion = float(heatmap[ys, xs].mean())
        regions.append(
            Region(
                x=int(x0 * cell_w),
                y=int(y0 * cell_h),
                width=int((x1 - x0) * cell_w),
                height=int((y1 - y0) * cell_h),
                label=label,
                suspicion=min(1.0, suspicion),
            )
        )

    regions.sort(key=lambda r: r.suspicion or 0, reverse=True)
    return regions[:6]


def error_level_analysis(image_bytes: bytes) -> ForensicMap:
    """
    Error Level Analysis.

    Recompress the image at a known quality and measure how much each area
    changes. Pixels that have been through one compression history respond to
    recompression differently from pixels pasted in from another source, so a
    spliced region lights up against its surroundings.

    Only meaningful on JPEG input -- a PNG has no compression history to
    disagree with, and reporting an ELA score for one would be fabricating
    evidence.
    """
    pil = Image.open(io.BytesIO(image_bytes))
    fmt = (pil.format or "").upper()

    if fmt not in ("JPEG", "JPG", "MPO"):
        return ForensicMap(
            heatmap=np.zeros((GRID_ROWS, GRID_COLS)),
            regions=[],
            mean=0.0,
            std=0.0,
            applicable=False,
            note=(
                f"Source is {fmt or 'an unknown format'}, not JPEG. Error Level "
                f"Analysis compares compression histories and has no meaning "
                f"without one, so it was not run."
            ),
        )

    rgb = pil.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=ELA_QUALITY)
    buf.seek(0)
    recompressed = Image.open(buf).convert("RGB")

    diff = np.abs(
        np.asarray(rgb, dtype=np.int16) - np.asarray(recompressed, dtype=np.int16)
    ).sum(axis=2)

    # Texture per cell, used to compare like with like below.
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    texture_px = cv2.magnitude(gx, gy)

    h, w = diff.shape
    heat = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float64)
    texture = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float64)
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            y0, y1 = int(r * h / GRID_ROWS), int((r + 1) * h / GRID_ROWS)
            x0, x1 = int(c * w / GRID_COLS), int((c + 1) * w / GRID_COLS)
            block = diff[y0:y1, x0:x1]
            # 95th percentile, not mean: a small edited patch inside a mostly
            # clean cell would be washed out by averaging.
            heat[r, c] = np.percentile(block, 95) if block.size else 0.0
            tblock = texture_px[y0:y1, x0:x1]
            texture[r, c] = float(tblock.mean()) if tblock.size else 0.0

    # --- texture-stratified comparison ------------------------------------
    # JPEG spends its error budget at edges, so a cell full of text ALWAYS has
    # a high compression residual. Comparing every cell against one global mean
    # therefore just rediscovers where the writing is -- which was this
    # detector's actual behaviour before this change: it flagged the text on a
    # blank synthetic document and scored it 21/100.
    #
    # Masking text out is not the answer either, because text is exactly where
    # documents get edited. Instead we compare each cell only against cells of
    # SIMILAR texture: a text cell whose residual is out of line with the other
    # text cells is meaningful, while a text cell that looks like every other
    # text cell is not.
    # Duplicate quantiles (a mostly-flat image) make digitize assign strata
    # arbitrarily, which silently destroys the like-for-like comparison. Fall
    # back to a single stratum when the texture distribution is degenerate.
    bins = np.unique(np.quantile(texture, [0.25, 0.5, 0.75]))
    bin_index = (
        np.digitize(texture, bins)
        if len(bins) >= 2
        else np.zeros_like(texture, dtype=int)
    )

    z = np.zeros_like(heat)
    for b in range(4):
        members = bin_index == b
        if members.sum() < 4:
            # Too few comparable cells to say anything about this stratum.
            continue
        values = heat[members]
        mu, sigma = float(values.mean()), float(values.std())
        if sigma < 1e-6:
            continue
        z[members] = (heat[members] - mu) / sigma

    mean, std = float(heat.mean()), float(heat.std())

    if float(np.percentile(heat, 95)) < MIN_ELA_ABSOLUTE_RESIDUAL:
        return ForensicMap(
            heatmap=np.zeros((GRID_ROWS, GRID_COLS)),
            regions=[],
            mean=mean,
            std=std,
            applicable=False,
            note=(
                "The image recompresses almost losslessly everywhere, so there "
                "is no compression-history variation to analyse. This happens "
                "with synthetic or heavily flattened images. No conclusion about "
                "tampering can be drawn from Error Level Analysis here."
            ),
        )

    # Only positive deviations matter: an area that compresses HARDER than its
    # peers has been through an extra encode. One that compresses more easily
    # has not.
    z = np.clip(z, 0, None)
    # A cell must ALSO clear the absolute floor -- being an outlier among
    # near-zero values is not evidence of anything.
    z = np.where(heat >= MIN_ELA_ABSOLUTE_RESIDUAL, z, 0.0)
    norm = z / (z.max() + 1e-6)
    threshold = (
        ELA_SIGMA_THRESHOLD / (z.max() + 1e-6) if z.max() > ELA_SIGMA_THRESHOLD else 1.1
    )

    regions = _cells_to_regions(
        norm, threshold, w, h, "compression residual out of line with similar areas"
    )
    return ForensicMap(heatmap=norm, regions=regions, mean=mean, std=std)


def noise_inconsistency(image_bytes: bytes) -> ForensicMap:
    """
    Local noise-variance analysis.

    Every capture device leaves a roughly uniform sensor-noise floor across the
    frame. Content pasted from another image carries that image's noise floor
    instead, so a splice shows up as a patch whose local variance disagrees
    with the rest of the document.

    Unlike ELA this works on any format, which makes it the fallback when a
    document arrives as PNG.
    """
    img = _to_cv(image_bytes)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # High-pass residual: subtract a median-blurred copy to strip real content
    # (edges, text, faces) and leave mostly sensor noise behind.
    residual = gray.astype(np.float32) - cv2.medianBlur(gray, 3).astype(np.float32)

    # Text and edges dominate the residual and swamp the sensor noise we
    # actually want to measure, so cells with heavy structure are excluded
    # rather than scored. Without this the detector simply reports "there is
    # writing here", which is true of every document and useful on none.
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    texture = cv2.magnitude(gx, gy)

    h, w = residual.shape
    heat = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float64)
    valid = np.zeros((GRID_ROWS, GRID_COLS), dtype=bool)

    cell_texture = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float64)
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            y0, y1 = int(r * h / GRID_ROWS), int((r + 1) * h / GRID_ROWS)
            x0, x1 = int(c * w / GRID_COLS), int((c + 1) * w / GRID_COLS)
            block = residual[y0:y1, x0:x1]
            heat[r, c] = float(block.std()) if block.size else 0.0
            tblock = texture[y0:y1, x0:x1]
            cell_texture[r, c] = float(tblock.mean()) if tblock.size else 0.0

    # Keep the flatter half of the document as the measurable population.
    texture_cut = float(np.median(cell_texture))
    valid = cell_texture <= max(texture_cut, 1e-6)
    if valid.sum() < 8:
        return ForensicMap(
            heatmap=np.zeros((GRID_ROWS, GRID_COLS)),
            regions=[],
            mean=0.0,
            std=0.0,
            applicable=False,
            note=(
                "The document is too densely printed to measure a sensor-noise "
                "floor: almost every area is dominated by text or graphics."
            ),
        )

    mean = float(heat[valid].mean())
    std = float(heat[valid].std())
    heat = np.where(valid, heat, mean)  # neutralise excluded cells

    # Deviation in EITHER direction is suspicious: a pasted region can be
    # noisier than its surroundings, but a smoothed or inpainted one is
    # markedly cleaner, and that is just as unnatural.
    deviation = np.abs(heat - mean)
    norm = deviation / (deviation.max() + 1e-6)
    threshold = (NOISE_SIGMA_THRESHOLD * std) / (deviation.max() + 1e-6) if std > 0 else 1.1

    regions = _cells_to_regions(norm, threshold, w, h, "inconsistent noise floor")
    return ForensicMap(heatmap=norm, regions=regions, mean=mean, std=std)


# A real copy-move moves a CONTIGUOUS PATCH, so the many keypoint pairs it
# creates all share nearly the same translation vector. Coincidental matches
# between repeated glyphs scatter across many different offsets. Requiring a
# dominant offset is what separates the two, and without it this detector
# fires on every text document -- the letter "A" appearing twenty times looks
# exactly like twenty duplications.
OFFSET_BIN_SIZE = 12          # pixels; tolerance for "same" translation
MIN_PAIRS_PER_OFFSET = 12     # pairs that must agree on one offset


def copy_move_detection(image_bytes: bytes, min_matches: int = 12) -> list[Region]:
    """
    Copy-move detection via ORB self-matching with translation-offset voting.

    A copy-move forgery duplicates a region of the SAME image -- cloning
    background over a date, or repeating a security pattern. Naive keypoint
    self-matching cannot detect this on a document, because printed text is
    full of genuinely identical shapes. The discriminator is GEOMETRY: a
    cloned patch produces a large cluster of matches sharing one translation
    vector, whereas repeated letters produce matches pointing everywhere.

    So we bin matches by their (dx, dy) offset and report only offsets that
    accumulate enough agreeing pairs to constitute a moved region.
    """
    img = _to_cv(image_bytes)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(nfeatures=5000)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None or len(keypoints) < 2 * min_matches:
        return []

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    # k=3 because a descriptor's nearest neighbour is always itself.
    raw = matcher.knnMatch(descriptors, descriptors, k=3)

    h, w = gray.shape
    # Ignore pairs closer than a tenth of the image: adjacent keypoints on the
    # same stroke or texture resemble each other for innocent reasons.
    min_distance = 0.1 * max(h, w)

    # Vote on quantised translation offsets.
    buckets: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for group in raw:
        for m in group:
            if m.queryIdx == m.trainIdx or m.distance > 24:
                continue
            x1, y1 = keypoints[m.queryIdx].pt
            x2, y2 = keypoints[m.trainIdx].pt
            dx, dy = x2 - x1, y2 - y1
            if float(np.hypot(dx, dy)) < min_distance:
                continue
            # Normalise direction so A->B and B->A land in the same bucket.
            if (dx, dy) < (0.0, 0.0):
                dx, dy = -dx, -dy
                x1, y1, x2, y2 = x2, y2, x1, y1
            key = (int(dx // OFFSET_BIN_SIZE), int(dy // OFFSET_BIN_SIZE))
            buckets.setdefault(key, []).append((x1, y1, x2, y2))

    # --- suppress periodic layout -------------------------------------
    # Identity documents are deliberately repetitive: evenly spaced field
    # rows, ruled lines, guilloche security patterns, microtext, and MRZ
    # filler runs. All of these generate a strong consistent offset and are
    # indistinguishable from a copy-move by geometry alone.
    #
    # The discriminator is HARMONICS. A genuine copy-move duplicates a patch
    # once, producing matches at a single offset k. A periodic structure
    # repeats, so it also produces matches at 2k, 3k, ... Finding those
    # harmonics means we are looking at the document's design, not an edit.
    populated = {k for k, v in buckets.items() if len(v) >= MIN_PAIRS_PER_OFFSET // 2}

    def is_periodic(key: tuple[int, int]) -> bool:
        kx, ky = key
        if kx == 0 and ky == 0:
            return False
        harmonics = 0
        for mult in (2, 3):
            probe = (kx * mult, ky * mult)
            # Allow one bin of slack, since binning quantises the offsets.
            for jitter_x in (-1, 0, 1):
                for jitter_y in (-1, 0, 1):
                    if (probe[0] + jitter_x, probe[1] + jitter_y) in populated:
                        harmonics += 1
                        break
                else:
                    continue
                break
        return harmonics >= 1

    regions: list[Region] = []
    for _key, pairs in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
        if len(pairs) < MIN_PAIRS_PER_OFFSET:
            continue
        if is_periodic(_key):
            continue

        # Report BOTH ends of the duplication -- the reviewer needs to see the
        # source and the copy to judge whether it is a real edit.
        src = np.array([(p[0], p[1]) for p in pairs], dtype=np.float32)
        dst = np.array([(p[2], p[3]) for p in pairs], dtype=np.float32)
        confidence = min(1.0, len(pairs) / 40.0)

        for pts, tag in ((src, "duplicated region (source)"), (dst, "duplicated region (copy)")):
            x0, y0 = pts.min(axis=0)
            x1_, y1_ = pts.max(axis=0)
            if (x1_ - x0) < 8 or (y1_ - y0) < 8:
                continue
            regions.append(
                Region(
                    x=int(x0),
                    y=int(y0),
                    width=int(x1_ - x0),
                    height=int(y1_ - y0),
                    label=tag,
                    suspicion=confidence,
                )
            )

        if len(regions) >= 4:
            break

    return regions


def analyze_metadata(image_bytes: bytes) -> list[Signal]:
    """
    EXIF inspection.

    Editing software routinely stamps its own name into the file. That is not
    proof of forgery -- people legitimately crop and rotate scans -- but a
    document that has been through an image editor deserves a closer look than
    one straight off a scanner.
    """
    signals: list[Signal] = []
    try:
        pil = Image.open(io.BytesIO(image_bytes))
        exif = pil.getexif()
    except Exception:
        return signals

    if not exif:
        signals.append(
            signal(
                code="forensics.metadata.absent",
                stage=Stage.FORENSICS,
                title="Image metadata",
                status=SignalStatus.WARN,
                severity=Severity.LOW,
                confidence=0.4,
                reason=(
                    "The image carries no EXIF metadata. Editors and messaging "
                    "apps commonly strip it, so this is weak evidence on its "
                    "own -- but an original camera capture usually retains it."
                ),
            )
        )
        return signals

    # 305 = Software, 271 = Make, 272 = Model
    software = str(exif.get(305, "")).strip()
    make = str(exif.get(271, "")).strip()
    model = str(exif.get(272, "")).strip()

    editors = (
        "photoshop", "gimp", "paint", "lightroom", "affinity",
        "pixlr", "canva", "snapseed", "picsart", "inkscape",
    )
    if software and any(e in software.lower() for e in editors):
        signals.append(
            signal(
                code="forensics.metadata.editor",
                stage=Stage.FORENSICS,
                title="Editing software in metadata",
                status=SignalStatus.WARN,
                severity=Severity.MEDIUM,
                confidence=0.85,
                reason=(
                    f"The file records {software!r} in its metadata, meaning it was "
                    f"saved by image-editing software rather than produced directly "
                    f"by a camera or scanner. This alone does not indicate forgery, "
                    f"but it does mean the pixels were re-authored at some point."
                ),
                evidence={"software": software, "make": make, "model": model},
            )
        )
    elif software or make:
        signals.append(
            signal(
                code="forensics.metadata.capture_device",
                stage=Stage.FORENSICS,
                title="Capture device metadata",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason=(
                    f"Metadata is consistent with direct capture "
                    f"(device: {make or 'unknown'} {model}, software: {software or 'none'})."
                ),
                evidence={"software": software, "make": make, "model": model},
            )
        )

    return signals


def run_forensics(
    image_bytes: bytes,
    *,
    enable_copy_move: bool = False,
    enable_ela: bool = False,
) -> list[Signal]:
    """
    Run the full multi-signal forensic suite.

    Each method emits its own Signal. They are deliberately NOT fused into a
    single "tampering score" here -- fusion is the Risk Engine's job, and
    keeping them separate means the reviewer can see that (say) ELA and noise
    analysis agreed on the photo region, which is far more persuasive than one
    blended number.
    """
    signals: list[Signal] = []

    try:
        img = _to_cv(image_bytes)
        img_h, img_w = img.shape[:2]
    except ValueError as exc:
        return [
            signal(
                code="forensics.decode_failed",
                stage=Stage.FORENSICS,
                title="Image decoding",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                reason=f"The image could not be decoded for forensic analysis: {exc}",
            )
        ]

    # --- Error Level Analysis (opt-in) ---
    #
    # Disabled by default for the same reason as copy-move, and on the same
    # evidence: measured behaviour, not caution. It reported FAIL on a blank
    # synthetic document (nothing there to tamper with) while reporting PASS on
    # a deliberately spliced one. A detector that is confident where there is
    # no document and silent where there is an edit is not yet a detector.
    #
    # The implementation is sound in principle -- texture-stratified comparison
    # is the right way to stop it merely rediscovering where the text is -- but
    # every threshold in it is currently a guess. Enabling it requires
    # calibration against labelled tampered documents; see docs/FORENSICS.md.
    if not enable_ela:
        signals.append(
            signal(
                code="forensics.ela.disabled",
                stage=Stage.FORENSICS,
                title="Error Level Analysis",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "Error Level Analysis did not run. It is disabled pending "
                    "calibration: in testing it flagged a blank synthetic image "
                    "as tampered while passing a spliced one, so its output "
                    "would mislead rather than inform."
                ),
            )
        )
    else:
        try:
            ela = error_level_analysis(image_bytes)
            if not ela.applicable:
                signals.append(
                    signal(
                        code="forensics.ela.not_applicable",
                        stage=Stage.FORENSICS,
                        title="Error Level Analysis",
                        status=SignalStatus.SKIP,
                        severity=Severity.INFO,
                        reason=ela.note,
                    )
                )
            elif ela.regions:
                top = ela.regions[0].suspicion or 0.0
                signals.append(
                    signal(
                        code="forensics.ela.anomaly",
                        stage=Stage.FORENSICS,
                        title="Error Level Analysis",
                        status=SignalStatus.WARN if top < 0.75 else SignalStatus.FAIL,
                        severity=Severity.HIGH if top >= 0.75 else Severity.MEDIUM,
                        confidence=min(0.9, 0.45 + top / 2),
                        reason=(
                            f"{len(ela.regions)} region(s) show a compression residual well "
                            f"above the rest of the document (peak suspicion {top:.0%}). "
                            f"Areas that were pasted in or re-saved separately carry a "
                            f"different compression history from the page around them. "
                            f"Holograms and glare can produce the same pattern, so treat "
                            f"this as a place to look, not a conclusion."
                        ),
                        evidence={
                            "regions_found": len(ela.regions),
                            "peak_suspicion": round(top, 3),
                            "grid_mean": round(ela.mean, 2),
                            "grid_std": round(ela.std, 2),
                        },
                        regions=ela.regions,
                    )
                )
            else:
                signals.append(
                    signal(
                        code="forensics.ela.clean",
                        stage=Stage.FORENSICS,
                        title="Error Level Analysis",
                        status=SignalStatus.PASS,
                        severity=Severity.INFO,
                        reason=(
                            "Compression residual is uniform across the document; no "
                            "region stands out as separately edited."
                        ),
                    )
                )
        except Exception as exc:
            signals.append(
                signal(
                    code="forensics.ela.error",
                    stage=Stage.FORENSICS,
                    title="Error Level Analysis",
                    status=SignalStatus.ERROR,
                    severity=Severity.LOW,
                    reason=f"Error Level Analysis could not complete: {exc}",
                )
            )

    # --- Noise consistency ---
    try:
        noise = noise_inconsistency(image_bytes)
        if noise.regions:
            top = noise.regions[0].suspicion or 0.0
            signals.append(
                signal(
                    code="forensics.noise.inconsistent",
                    stage=Stage.FORENSICS,
                    title="Noise consistency",
                    status=SignalStatus.WARN,
                    severity=Severity.MEDIUM,
                    confidence=min(0.85, 0.4 + top / 2),
                    reason=(
                        f"{len(noise.regions)} region(s) have a sensor-noise profile "
                        f"that disagrees with the rest of the image. A single capture "
                        f"has one noise floor throughout, so content carrying a "
                        f"different one was likely introduced from elsewhere -- or "
                        f"smoothed over to hide something."
                    ),
                    evidence={
                        "regions_found": len(noise.regions),
                        "peak_suspicion": round(top, 3),
                    },
                    regions=noise.regions,
                )
            )
        else:
            signals.append(
                signal(
                    code="forensics.noise.consistent",
                    stage=Stage.FORENSICS,
                    title="Noise consistency",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason="Sensor-noise characteristics are uniform across the document.",
                )
            )
    except Exception as exc:
        signals.append(
            signal(
                code="forensics.noise.error",
                stage=Stage.FORENSICS,
                title="Noise consistency",
                status=SignalStatus.ERROR,
                severity=Severity.LOW,
                reason=f"Noise analysis could not complete: {exc}",
            )
        )

    # --- Copy-move (opt-in) ---
    #
    # Disabled by default after measurement, not by guesswork. Run against
    # clean, spliced and cloned versions of the SAME document, this detector
    # returned an identical region for all three: it locks onto the regular
    # spacing of the document's own field rows and never sees the edit.
    # Identity documents are deliberately repetitive -- ruled rows, guilloche
    # backgrounds, microtext, MRZ filler runs -- and that repetition is
    # geometrically indistinguishable from a cloned patch.
    #
    # It is kept, not deleted, because the approach is sound once calibrated
    # against labelled tampered documents (CASIA v2, CoMoFoD, DocTamper).
    # Until that calibration exists, emitting its output would be presenting
    # noise as evidence.
    if not enable_copy_move:
        signals.append(
            signal(
                code="forensics.copy_move.disabled",
                stage=Stage.FORENSICS,
                title="Copy-move detection",
                status=SignalStatus.SKIP,
                severity=Severity.INFO,
                reason=(
                    "Copy-move detection did not run. It is disabled pending "
                    "calibration against a labelled tampered-document dataset: "
                    "in testing it returned the same region for clean, spliced "
                    "and cloned copies of one document, so it currently adds no "
                    "information that would help a reviewer."
                ),
            )
        )
        return signals + analyze_metadata(image_bytes)

    try:
        cm_regions = copy_move_detection(image_bytes)
        if cm_regions:
            signals.append(
                signal(
                    code="forensics.copy_move.detected",
                    stage=Stage.FORENSICS,
                    title="Copy-move detection",
                    # WARN, not FAIL, and deliberately low confidence. Identity
                    # documents are full of intentional repetition, so this
                    # detector's false-positive rate on real documents is high
                    # even after periodicity suppression. It earns its place as
                    # a pointer for the reviewer's eye, not as a verdict -- and
                    # the Risk Engine weights it accordingly.
                    status=SignalStatus.WARN,
                    severity=Severity.MEDIUM,
                    confidence=0.45,
                    reason=(
                        f"Found {len(cm_regions)} area(s) that resemble duplicates of "
                        f"other parts of the SAME image, after discounting the regular "
                        f"repeating structure a document normally contains. Cloning a "
                        f"patch over an existing value is a common edit because it "
                        f"preserves surrounding texture. Security backgrounds and "
                        f"repeated layout can also trigger this, so confirm visually "
                        f"before treating it as a finding."
                    ),
                    evidence={"duplicate_clusters": len(cm_regions)},
                    regions=cm_regions,
                )
            )
        else:
            signals.append(
                signal(
                    code="forensics.copy_move.clean",
                    stage=Stage.FORENSICS,
                    title="Copy-move detection",
                    status=SignalStatus.PASS,
                    severity=Severity.INFO,
                    reason="No duplicated regions were found within the image.",
                )
            )
    except Exception as exc:
        signals.append(
            signal(
                code="forensics.copy_move.error",
                stage=Stage.FORENSICS,
                title="Copy-move detection",
                status=SignalStatus.ERROR,
                severity=Severity.LOW,
                reason=f"Copy-move detection could not complete: {exc}",
            )
        )

    # --- Metadata ---
    signals.extend(analyze_metadata(image_bytes))

    return signals
