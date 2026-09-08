"""
Triage mislabelled documents in the raw dataset.

Public document datasets are routinely mislabelled -- the Roboflow "Aadhaar"
collection in this project contains PAN cards, which then train and evaluate
the wrong rulebook. This script reads each document and reports what it
actually is.

Two design decisions that matter:

* **Evidence must be institutional text, not a number pattern.** A PAN number
  is five letters, four digits, one letter -- a shape OCR noise can produce by
  accident from any dense text. Moving a file on that basis alone would create
  new mislabelling while claiming to fix it. So a reclassification requires
  the printed issuer wording ("INCOME TAX DEPARTMENT", "UNIQUE IDENTIFICATION
  AUTHORITY"), which OCR does not invent.

* **One OCR pass per SOURCE document, not per file.** Roboflow ships 8-17
  augmented copies of each original. They are the same document, so
  classifying one and applying the result to its whole family is both correct
  and an order of magnitude cheaper.

Usage:
    python scripts/triage_raw_dataset.py --folder aadhaar            # report only
    python scripts/triage_raw_dataset.py --folder aadhaar --apply    # then move
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
RAW = BACKEND / "data" / "datasets" / "raw"

sys.path.insert(0, str(BACKEND))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Roboflow encodes augmentations as <source>.rf.<32-hex>.<ext>. Stripping that
# suffix recovers which original a file came from.
_ROBOFLOW_SUFFIX = re.compile(r"\.rf\.[0-9a-f]{6,}$")

# Printed issuer wording. These are what a document says about itself, and are
# far harder for OCR to fabricate than a bare alphanumeric pattern.
_STRONG_EVIDENCE: dict[str, list[re.Pattern[str]]] = {
    "pan": [
        re.compile(r"INCOME\s*TAX\s*DEPARTMENT", re.I),
        re.compile(r"PERMANENT\s*ACCOUNT\s*NUMBER", re.I),
    ],
    "aadhaar": [
        re.compile(r"UNIQUE\s*IDENTIFICATION\s*AUTHORITY", re.I),
        re.compile(r"\bAADHAAR\b", re.I),
        re.compile(r"आधार"),
        re.compile(r"\bमेरा\s*आधार\b"),
    ],
    "driving_licence": [
        re.compile(r"DRIVING\s*LICEN[CS]E", re.I),
        re.compile(r"TRANSPORT\s*DEPARTMENT", re.I),
    ],
    "voter_id": [
        re.compile(r"ELECTION\s*COMMISSION", re.I),
        re.compile(r"ELECTOR'?S?\s*PHOTO\s*IDENTITY", re.I),
    ],
    "passport": [
        re.compile(r"^P[<A-Z][A-Z]{3}[A-Z<]{10,}$", re.M),
        re.compile(r"\bPASSPORT\b", re.I),
    ],
}


def source_key(path: Path) -> str:
    """Which original document a (possibly augmented) file came from."""
    return _ROBOFLOW_SUFFIX.sub("", path.stem)


def strong_matches(text: str) -> dict[str, list[str]]:
    """Return every document type whose printed issuer wording appears."""
    found: dict[str, list[str]] = {}
    for doc_type, patterns in _STRONG_EVIDENCE.items():
        hits = [m.group(0)[:40] for p in patterns if (m := p.search(text))]
        if hits:
            found[doc_type] = hits
    return found


def triage(folder: str, limit: int | None = None) -> dict:
    import logging

    logging.disable(logging.WARNING)
    from app.pipeline.stages.ocr import get_default_provider

    source_dir = RAW / folder
    if not source_dir.is_dir():
        raise SystemExit(f"no such folder: {source_dir}")

    families: dict[str, list[Path]] = defaultdict(list)
    for f in sorted(source_dir.iterdir()):
        if f.suffix.lower() in IMAGE_SUFFIXES:
            families[source_key(f)].append(f)

    sources = sorted(families)
    if limit:
        sources = sources[:limit]

    provider = get_default_provider(use_gpu=False)
    print(f"{len(families)} source documents across "
          f"{sum(len(v) for v in families.values())} files; reading {len(sources)}\n")

    results: dict[str, dict] = {}
    for i, key in enumerate(sources, 1):
        representative = families[key][0]
        try:
            text = provider.read(representative.read_bytes()).full_text
        except Exception as exc:  # noqa: BLE001 -- one bad file must not stop the sweep
            results[key] = {"verdict": "error", "error": str(exc), "files": len(families[key])}
            continue

        evidence = strong_matches(text)

        # A file stays put unless exactly one type is evidenced AND it is not
        # the folder it already lives in. Ambiguity means leave it alone: a
        # wrong move is worse than a missed one, because it is invisible.
        others = {t: e for t, e in evidence.items() if t != folder}
        if len(evidence) == 1 and others:
            verdict = next(iter(others))
        elif folder in evidence:
            verdict = folder
        else:
            verdict = "unclear"

        results[key] = {
            "verdict": verdict,
            "evidence": evidence,
            "files": len(families[key]),
        }

        if i % 25 == 0 or i == len(sources):
            print(f"  read {i}/{len(sources)}")

    return {"folder": folder, "families": {k: [str(p) for p in v] for k, v in families.items()},
            "results": results}


def report(data: dict) -> None:
    results = data["results"]
    by_verdict: dict[str, list[str]] = defaultdict(list)
    for key, r in results.items():
        by_verdict[r["verdict"]].append(key)

    print(f"\n{'verdict':<20} {'sources':>8} {'files':>8}")
    print("-" * 40)
    for verdict, keys in sorted(by_verdict.items(), key=lambda kv: -len(kv[1])):
        files = sum(results[k]["files"] for k in keys)
        print(f"{verdict:<20} {len(keys):>8} {files:>8}")

    misfiled = {v: k for v, k in by_verdict.items() if v not in (data["folder"], "unclear", "error")}
    if misfiled:
        print("\nMisfiled documents found:")
        for verdict, keys in misfiled.items():
            files = sum(results[k]["files"] for k in keys)
            print(f"  -> {verdict}: {len(keys)} source(s), {files} file(s)")
            for k in keys[:5]:
                ev = results[k].get("evidence", {}).get(verdict, [])
                print(f"       {k[:52]:<54} {ev[:2]}")
            if len(keys) > 5:
                print(f"       ... and {len(keys) - 5} more")


def apply_moves(data: dict) -> None:
    folder = data["folder"]
    results, families = data["results"], data["families"]

    moved = 0
    for key, r in results.items():
        verdict = r["verdict"]
        if verdict in (folder, "unclear", "error"):
            continue
        target = RAW / verdict
        target.mkdir(parents=True, exist_ok=True)
        for path_str in families[key]:
            src = Path(path_str)
            if not src.exists():
                continue
            dest = target / src.name
            if dest.exists():
                dest = target / f"{src.stem}__from_{folder}{src.suffix}"
            shutil.move(str(src), str(dest))
            moved += 1
    print(f"\nMoved {moved} file(s).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and relocate mislabelled documents.")
    parser.add_argument("--folder", default="aadhaar", help="raw/ subfolder to triage")
    parser.add_argument("--limit", type=int, default=None, help="only read N sources")
    parser.add_argument("--apply", action="store_true", help="actually move files")
    parser.add_argument(
        "--cache",
        default=str(REPO_ROOT / "backend" / "data" / "datasets" / "triage.json"),
        help="where to write/read the scan result",
    )
    args = parser.parse_args()

    cache = Path(args.cache)

    if args.apply and cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("folder") != args.folder:
            raise SystemExit(f"cache is for '{data.get('folder')}', not '{args.folder}'")
        print(f"Using scan from {cache}")
    else:
        data = triage(args.folder, args.limit)
        cache.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nScan written to {cache}")

    report(data)

    if args.apply:
        apply_moves(data)
    else:
        print("\nNothing moved. Re-run with --apply to move the misfiled files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
