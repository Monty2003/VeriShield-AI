"""
Measure how well VeriShield actually performs on your documents.

Reads every image under backend/data/datasets/raw/<type>/, treats the folder
name as ground truth, and reports what the system got right and wrong.

What it measures, and why each one is here
------------------------------------------
* **Classification accuracy**, with a confusion matrix. A wrong type is worse
  than no type, because the type selects the rulebook -- so misclassifications
  and UNKNOWNs are counted separately rather than lumped into one error rate.

* **Extraction rate.** How often the fields the rulebooks depend on were
  actually recovered. A validator that never receives a document number cannot
  validate anything, and that failure is invisible in an accuracy number.

* **Decision distribution.** How many documents were accepted, escalated, or
  rejected. On a set of genuine documents, every rejection is a false positive
  and worth looking at individually.

* **Confidence calibration.** The system reports a confidence with every
  assessment. This checks whether that number means anything: high-confidence
  results should be right more often than low-confidence ones. A confidence
  that does not track correctness is decoration.

* **Timing per stage**, so it is clear where the seconds go.

A note on what this can and cannot tell you
-------------------------------------------
Every document here is presumed GENUINE. So this measures the false-positive
side only: how often the system troubles a real person about a real document.
It says nothing about whether forgeries are caught, because there are no
forgeries in the set. Those are different questions and need different data.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

RAW = BACKEND / "data" / "datasets" / "raw"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# Fields each rulebook needs before it can do anything meaningful.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "aadhaar": ["document_number"],
    "pan": ["document_number", "full_name"],
    "certificate": ["raw_text"],
    "passport": ["document_number", "date_of_birth"],
}


def collect() -> list[tuple[Path, str]]:
    found = []
    if not RAW.exists():
        return found
    for type_dir in sorted(RAW.iterdir()):
        if not type_dir.is_dir():
            continue
        for path in sorted(type_dir.iterdir()):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                found.append((path, type_dir.name))
    return found


def bar(value: float, width: int = 24) -> str:
    filled = int(round(value * width))
    return "#" * filled + "." * (width - filled)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate VeriShield on labelled documents.")
    parser.add_argument("--limit", type=int, default=None, help="Only evaluate N documents.")
    parser.add_argument("--no-face", action="store_true", help="Skip face detection (much faster).")
    parser.add_argument("--json", type=str, default=None, help="Write results to this path.")
    args = parser.parse_args()

    import logging

    logging.disable(logging.WARNING)

    from app.pipeline.orchestrator import analyze_document
    from app.pipeline.stages.face import NullFaceProvider
    from app.registry.authority import SyntheticRegistry

    samples = collect()
    if args.limit:
        samples = samples[: args.limit]

    if not samples:
        print(f"No documents found under {RAW}.")
        print("Put images in raw/<type>/ -- the folder name is the ground-truth label.")
        return 1

    registry = SyntheticRegistry.from_file(BACKEND / "data" / "synthetic_registry.json")
    face_provider = NullFaceProvider() if args.no_face else None

    print(f"Evaluating {len(samples)} documents from {RAW}\n")

    confusion: dict[str, Counter] = defaultdict(Counter)
    sides = Counter()
    decisions = Counter()
    extraction: dict[str, list[bool]] = defaultdict(list)
    timings: dict[str, list[float]] = defaultdict(list)
    confidence_buckets: dict[str, list[bool]] = defaultdict(list)
    rejects: list[tuple[str, str, str]] = []
    rows = []

    started = time.perf_counter()
    for i, (path, truth) in enumerate(samples, 1):
        analysis = analyze_document(
            path.read_bytes(),
            path.name,
            registry=registry,
            face_provider=face_provider,
        )

        predicted = analysis.document_type.value
        confusion[truth][predicted] += 1
        sides[analysis.side.value] += 1
        decisions[analysis.risk.decision.value] += 1

        correct = predicted == truth
        # Only the front of a document carries the fields a rulebook needs, so
        # scoring extraction on reverse sides would penalise the system for
        # correctly reporting that a back has no identity data on it.
        if correct and analysis.side.value == "front":
            for field in REQUIRED_FIELDS.get(truth, []):
                extraction[f"{truth}.{field}"].append(
                    getattr(analysis.fields, field).present
                )

        for stage, ms in analysis.processing_ms.items():
            timings[stage].append(ms)

        band = (
            "high (>=0.8)"
            if analysis.risk.confidence >= 0.8
            else "medium (0.5-0.8)"
            if analysis.risk.confidence >= 0.5
            else "low (<0.5)"
        )
        confidence_buckets[band].append(correct)

        if analysis.risk.decision.value == "reject":
            reason = analysis.risk.top_reasons[0] if analysis.risk.top_reasons else ""
            rejects.append((path.name, truth, reason))

        rows.append(
            {
                "file": path.name,
                "truth": truth,
                "predicted": predicted,
                "correct": correct,
                "side": analysis.side.value,
                "type_confidence": round(analysis.type_confidence, 3),
                "risk": analysis.risk.score,
                "decision": analysis.risk.decision.value,
                "evidence_confidence": analysis.risk.confidence,
            }
        )

        if i % 10 == 0 or i == len(samples):
            print(f"  {i}/{len(samples)}")

    elapsed = time.perf_counter() - started

    # ---- classification -------------------------------------------------
    print(f"\n{'=' * 72}\nCLASSIFICATION\n{'=' * 72}")
    total = correct_total = 0
    for truth in sorted(confusion):
        counts = confusion[truth]
        n = sum(counts.values())
        hits = counts.get(truth, 0)
        total += n
        correct_total += hits
        wrong = {k: v for k, v in counts.items() if k != truth}
        print(f"  {truth:<14} {hits}/{n}  {bar(hits / n)}  {hits / n:.0%}")
        if wrong:
            # UNKNOWN and a wrong type are different failures: one declines to
            # answer, the other answers incorrectly and applies the wrong rules.
            for k, v in sorted(wrong.items(), key=lambda kv: -kv[1]):
                kind = "declined" if k == "unknown" else "MISCLASSIFIED"
                print(f"                    -> {v} {kind} as {k}")
    print(f"\n  overall: {correct_total}/{total} = {correct_total / total:.1%}")

    print(f"\n  sides detected: {dict(sides)}")

    # ---- extraction -----------------------------------------------------
    print(f"\n{'=' * 72}\nFIELD EXTRACTION (front sides of correctly-typed documents)\n{'=' * 72}")
    if extraction:
        for key in sorted(extraction):
            values = extraction[key]
            rate = sum(values) / len(values)
            print(f"  {key:<28} {sum(values)}/{len(values)}  {bar(rate)}  {rate:.0%}")
    else:
        print("  nothing to measure")

    # ---- decisions ------------------------------------------------------
    print(f"\n{'=' * 72}\nDECISIONS\n{'=' * 72}")
    for decision, count in decisions.most_common():
        print(f"  {decision:<16} {count:>4}  {bar(count / len(samples))}  {count / len(samples):.0%}")
    print(
        "\n  Every document here is presumed genuine, so each 'reject' is a"
        "\n  FALSE POSITIVE -- a real person refused over a real document."
    )
    if rejects:
        print(f"\n  {len(rejects)} rejection(s) to investigate:")
        for name, truth, reason in rejects[:10]:
            print(f"    {name} ({truth}): {reason[:88]}")
    else:
        print("\n  No false positives.")

    # ---- confidence calibration ----------------------------------------
    print(f"\n{'=' * 72}\nCONFIDENCE CALIBRATION\n{'=' * 72}")
    print("  Does the reported confidence track whether the answer was right?")
    print(f"\n  {'confidence band':<20} {'n':>4}  {'correct':>8}")
    for band in ("high (>=0.8)", "medium (0.5-0.8)", "low (<0.5)"):
        values = confidence_buckets.get(band, [])
        if not values:
            continue
        rate = sum(values) / len(values)
        print(f"  {band:<20} {len(values):>4}  {rate:>7.0%}  {bar(rate)}")
    print(
        "\n  Useful calibration means the high band scores better than the low"
        "\n  one. If they are equal, the confidence number carries no information."
    )

    # ---- timing ---------------------------------------------------------
    print(f"\n{'=' * 72}\nTIMING (milliseconds per document)\n{'=' * 72}")
    print(f"  {'stage':<12} {'median':>9} {'mean':>9} {'max':>9}")
    for stage in sorted(timings, key=lambda s: -statistics.mean(timings[s])):
        values = timings[stage]
        print(
            f"  {stage:<12} {statistics.median(values):>9.0f} "
            f"{statistics.mean(values):>9.0f} {max(values):>9.0f}"
        )
    print(f"\n  total wall time: {elapsed:.1f}s for {len(samples)} documents "
          f"({elapsed / len(samples):.1f}s each)")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "documents": rows,
                    "classification_accuracy": correct_total / total,
                    "decisions": dict(decisions),
                    "elapsed_seconds": round(elapsed, 2),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n  written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
