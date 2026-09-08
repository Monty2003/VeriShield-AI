"""
Calibrate the forensic detectors against labelled data.

This is the script that closes docs/FORENSICS.md. ELA and copy-move were
disabled because every threshold in them was a guess; this measures what they
actually do on clean and tampered copies of the same documents and reports
whether they separate the two at all.

It reports three things, and the third is the one that decides:

  * **Separation.** Score distributions for clean vs tampered. If they overlap
    completely the detector carries no information and no threshold can rescue
    it -- which is exactly what was found before, when it returned an identical
    result for clean, spliced and cloned copies of one document.

  * **ROC.** True and false positive rates across candidate thresholds.

  * **Localisation.** Whether a detection lands on the region that was actually
    edited, measured against the pixel masks. A detector that fires on the
    right documents but the wrong areas is not usable evidence: the whole point
    is to show a reviewer WHERE to look, and a confident box over the wrong
    field is worse than no box.

Operating point: identity verification is asymmetric. A false accusation costs
a real person far more than a missed forgery costs the system, which falls back
to a human reviewer anyway. So the threshold is chosen at a low false-positive
rate rather than at maximum accuracy.

Usage:
    python scripts/calibrate_forensics.py
    python scripts/calibrate_forensics.py --target-fpr 0.05
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

GENERATED = BACKEND / "data" / "datasets" / "generated"

# Below this many samples per class the numbers are not worth acting on: a
# threshold fitted to a handful of images describes those images, not the
# detector.
MIN_SAMPLES_PER_CLASS = 10

# A detection counts as correctly localised when it overlaps the edited region
# at all. Deliberately lenient -- the reviewer needs to be pointed at the right
# field, not handed a pixel-perfect segmentation.
MIN_LOCALISATION_OVERLAP = 0.10


@dataclass
class DetectorScores:
    clean: list[float] = field(default_factory=list)
    tampered: list[float] = field(default_factory=list)
    localised: int = 0
    tampered_with_regions: int = 0
    errors: list[str] = field(default_factory=list)


def load_manifest() -> list[dict]:
    path = GENERATED / "manifest.jsonl"
    if not path.exists():
        raise SystemExit(
            f"No generated dataset found at {path}.\n"
            f"Run: python scripts/generate_tampered_dataset.py --variants 3"
        )
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def region_overlaps_mask(region, mask: np.ndarray) -> float:
    """Fraction of a reported region that lies inside the true edited area."""
    h, w = mask.shape[:2]
    x0, y0 = max(0, region.x), max(0, region.y)
    x1, y1 = min(w, region.x + region.width), min(h, region.y + region.height)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    window = mask[y0:y1, x0:x1]
    return float((window > 0).mean())


def roc_points(clean: list[float], tampered: list[float]) -> list[tuple[float, float, float]]:
    """
    (threshold, true positive rate, false positive rate) across the score range.

    Thresholds are drawn from the observed scores themselves rather than a
    fixed grid, so the curve reflects the data instead of an arbitrary
    resolution.
    """
    thresholds = sorted({*clean, *tampered})
    points = []
    for t in thresholds:
        tpr = sum(1 for s in tampered if s >= t) / max(len(tampered), 1)
        fpr = sum(1 for s in clean if s >= t) / max(len(clean), 1)
        points.append((t, tpr, fpr))
    return points


def summarise(name: str, scores: DetectorScores, target_fpr: float) -> dict:
    clean, tampered = scores.clean, scores.tampered
    print(f"\n{'=' * 74}\n{name}\n{'=' * 74}")

    if len(clean) < MIN_SAMPLES_PER_CLASS or len(tampered) < MIN_SAMPLES_PER_CLASS:
        print(f"  Not enough data: {len(clean)} clean, {len(tampered)} tampered "
              f"(need {MIN_SAMPLES_PER_CLASS} of each).")
        return {"detector": name, "verdict": "insufficient_data"}

    def stats(values: list[float]) -> str:
        arr = np.array(values)
        return (f"n={len(arr):<4} mean={arr.mean():>7.3f}  median={np.median(arr):>7.3f}  "
                f"p95={np.percentile(arr, 95):>7.3f}  max={arr.max():>7.3f}")

    print(f"  clean    : {stats(clean)}")
    print(f"  tampered : {stats(tampered)}")

    # Separation first. Without it, no threshold matters.
    overlap_low = max(min(clean), min(tampered))
    overlap_high = min(max(clean), max(tampered))
    separated = np.median(tampered) > np.percentile(clean, 95)

    points = roc_points(clean, tampered)
    usable = [p for p in points if p[2] <= target_fpr]
    best = max(usable, key=lambda p: p[1]) if usable else None

    print(f"\n  score ranges overlap between {overlap_low:.3f} and {overlap_high:.3f}")
    print(f"  tampered median above clean p95: {'YES' if separated else 'NO'}")

    if best is None or best[1] == 0.0:
        print(f"\n  VERDICT: no usable threshold at FPR <= {target_fpr:.0%}.")
        print("  The detector does not separate tampered from clean on this data.")
        print("  It must stay disabled -- a detector with no discriminating power")
        print("  reports noise, and noise presented as evidence is worse than silence.")
        return {"detector": name, "verdict": "no_separation"}

    threshold, tpr, fpr = best
    print(f"\n  best operating point at FPR <= {target_fpr:.0%}:")
    print(f"     threshold = {threshold:.3f}   TPR = {tpr:.1%}   FPR = {fpr:.1%}")

    if scores.tampered_with_regions:
        rate = scores.localised / scores.tampered_with_regions
        print(f"  localisation: {scores.localised}/{scores.tampered_with_regions} "
              f"detections landed on the edited region ({rate:.0%})")
    else:
        rate = 0.0
        print("  localisation: no regions were reported on any tampered image")

    verdict = "usable" if tpr >= 0.3 and rate >= 0.5 else "weak"
    print(f"\n  VERDICT: {verdict.upper()}")
    if verdict == "weak":
        print("  Detects too little, or points at the wrong place when it does.")

    return {
        "detector": name,
        "verdict": verdict,
        "threshold": round(threshold, 4),
        "tpr": round(tpr, 4),
        "fpr": round(fpr, 4),
        "localisation_rate": round(rate, 4),
        "n_clean": len(clean),
        "n_tampered": len(tampered),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate forensic detectors.")
    parser.add_argument("--target-fpr", type=float, default=0.10,
                        help="Highest acceptable false-positive rate (default 0.10).")
    args = parser.parse_args()

    from app.pipeline.stages.forensics import (
        copy_move_detection,
        error_level_analysis,
        noise_inconsistency,
    )

    records = load_manifest()
    print(f"Loaded {len(records)} records from the generated dataset.")

    detectors: dict[str, DetectorScores] = defaultdict(DetectorScores)

    for i, record in enumerate(records, 1):
        path = GENERATED / record["image"]
        if not path.exists():
            continue
        data = path.read_bytes()
        tampered = record["label"] == "tampered"

        mask = None
        if tampered and record.get("mask"):
            mask_path = GENERATED / record["mask"]
            if mask_path.exists():
                mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)

        for name, fn in (
            ("Error Level Analysis", error_level_analysis),
            ("Noise consistency", noise_inconsistency),
        ):
            scores = detectors[name]
            try:
                result = fn(data)
            except Exception as exc:  # noqa: BLE001
                scores.errors.append(f"{path.name}: {exc}")
                continue
            if not result.applicable:
                continue

            (scores.tampered if tampered else scores.clean).append(result.peak_score)

            if tampered and mask is not None and result.regions:
                scores.tampered_with_regions += 1
                if any(
                    region_overlaps_mask(r, mask) >= MIN_LOCALISATION_OVERLAP
                    for r in result.regions
                ):
                    scores.localised += 1

        # Copy-move reports pair clusters rather than a field; its score is how
        # many duplicated regions survived periodicity suppression.
        scores = detectors["Copy-move detection"]
        try:
            regions = copy_move_detection(data)
        except Exception as exc:  # noqa: BLE001
            scores.errors.append(f"{path.name}: {exc}")
        else:
            (scores.tampered if tampered else scores.clean).append(float(len(regions)))
            if tampered and mask is not None and regions:
                scores.tampered_with_regions += 1
                if any(region_overlaps_mask(r, mask) >= MIN_LOCALISATION_OVERLAP for r in regions):
                    scores.localised += 1

        if i % 20 == 0 or i == len(records):
            print(f"  scored {i}/{len(records)}")

    results = [summarise(name, s, args.target_fpr) for name, s in detectors.items()]

    out = GENERATED.parent / "calibration.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWritten to {out}")

    usable = [r for r in results if r.get("verdict") == "usable"]
    print(f"\n{'=' * 74}")
    if usable:
        print("Detectors that earned their place:")
        for r in usable:
            print(f"  {r['detector']}: threshold {r['threshold']}, "
                  f"TPR {r['tpr']:.0%} at FPR {r['fpr']:.0%}")
    else:
        print("No detector reached a usable operating point on this data.")
        print("They stay disabled. That is a measurement, not a failure to try:")
        print("a detector that cannot separate tampered from clean has nothing")
        print("to contribute, and saying so is more useful than shipping it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
