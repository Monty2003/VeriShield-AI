"""
Forgeries in the front-and-back flow.

scripts/evaluate.py measures each forged Aadhaar front on its own, and found
most of them auto-accepted: nothing printed on an Aadhaar except the number
carries a checksum, so an edited name, date of birth or photograph leaves
nothing on the front to contradict it. The fix is policy -- an Aadhaar is not
accepted until its signed QR has been compared -- and this script measures
what that comparison is actually worth, on the same forgeries.

Each forged front is submitted together with the GENUINE back of the same card,
exactly as a forger holding a real card would submit it. A front and a back are
paired only when the back's QR names the same number (last four digits) as the
front prints; nothing is paired by guesswork.

As in evaluate.py, a forgery counts as CAUGHT only if the clean pair was
accepted and the forged pair was not.

Two sets are measured, because they test different things:

  generated   the synthetic set from generate_tampered_dataset.py. Its edits
              are pixel-level -- a text region re-encoded and pasted back, a
              region copied, inpainted, or spliced from another document -- at
              a RANDOM text region. They rarely change what the card says, so
              they test image forensics, and the QR has nothing to contradict.

  controlled  the edits a forger actually makes, applied here to exactly the
              fields the QR vouches for: the printed date of birth, the number
              (rewritten to another checksum-valid one), and the portrait
              (replaced, at the box the face detector itself finds, with a
              different person's). Each edit is confirmed to have taken -- OCR
              reads the new value, or the new face really is someone else --
              before it is counted; an edit the pipeline cannot even read is
              reported separately rather than credited as a catch.

Nothing identifying is printed or saved: file names of the generated set, counts
and signal codes only. Backs are referred to by position, never by file name.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import GENERATED, IMAGE_SUFFIXES, RAW, TAMPERED_NAME, rate  # noqa: E402

def resolve(source: str) -> Path | None:
    for base in (GENERATED.parent, BACKEND, REPO_ROOT):
        path = base / source
        if path.exists():
            return path
    return None


DATE = re.compile(r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b")
NUMBER = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")


def rewrite_line(img, region, text: str):
    """Paint one OCR line over with its own background and print `text` there."""
    import cv2
    import numpy as np

    pad = max(2, region.height // 6)
    x0, y0 = max(0, region.x - pad), max(0, region.y - pad)
    x1 = min(img.shape[1], region.x + region.width + pad)
    y1 = min(img.shape[0], region.y + region.height + pad)
    border = np.concatenate(
        [img[y0, x0:x1], img[y1 - 1, x0:x1], img[y0:y1, x0], img[y0:y1, x1 - 1]]
    )
    out = img.copy()
    out[y0:y1, x0:x1] = np.median(border, axis=0)

    font, thickness = cv2.FONT_HERSHEY_SIMPLEX, max(1, region.height // 12)
    scale = cv2.getFontScaleFromHeight(font, max(8, int(region.height * 0.75)), thickness)
    (width, height), _ = cv2.getTextSize(text, font, scale, thickness)
    if width > x1 - x0:
        scale *= (x1 - x0) / width
        (width, height), _ = cv2.getTextSize(text, font, scale, thickness)
    baseline_y = y0 + (y1 - y0 + height) // 2
    cv2.putText(out, text, (x0, baseline_y), font, scale, (25, 25, 25), thickness, cv2.LINE_AA)
    return out


def another_valid_number(digits: str, rng: random.Random) -> str:
    """Same first eight digits, different last four, checksum still valid."""
    from app.rules.aadhaar import verhoeff_checksum

    while True:
        body = digits[:8] + "".join(rng.choice("0123456789") for _ in range(3))
        check = next(d for d in "0123456789" if verhoeff_checksum(body + d) == 0)
        if body[8:] + check != digits[8:]:
            number = body + check
            return f"{number[:4]} {number[4:8]} {number[8:]}"


def single_image_decision(data: bytes, registry, analyze_document, faces=None) -> str:
    """The image judged on its own, as it would have been before the QR policy."""
    from app.core.config import settings

    settings.aadhaar_require_qr = False
    try:
        return analyze_document(
            data, "forged.jpg", registry=registry, face_provider=faces
        ).risk.decision.value
    finally:
        settings.aadhaar_require_qr = True


def adverse(result) -> set[str]:
    """Codes that count against a case: its blocks and every failing signal."""
    codes = set(result.overall_risk.blocking_codes)
    codes |= {
        s.code
        for s in result.cross_document_signals
        if s.status.value in ("fail", "error", "warn")
    }
    for doc in result.documents:
        codes |= {s.code for s in doc.signals if s.status.value in ("fail", "error", "warn")}
    return codes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-face", action="store_true", help="Skip face models (no photo check).")
    parser.add_argument("--json", type=str, default=None, help="Write results to this path.")
    args = parser.parse_args()

    import logging

    logging.disable(logging.WARNING)

    from app.pipeline.orchestrator import analyze_document, verify_case
    from app.pipeline.stages.face import NullFaceProvider
    from app.registry.authority import SyntheticRegistry
    from app.rules.aadhaar_qr import read_aadhaar_qr

    registry = SyntheticRegistry.from_file(BACKEND / "data" / "synthetic_registry.json")
    faces = NullFaceProvider() if args.no_face else None

    manifest = [
        json.loads(line)
        for line in (GENERATED / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    clean_source: dict[str, Path] = {}
    for row in manifest:
        if row["doc_type"] == "aadhaar" and row["label"] == "clean":
            source = resolve(row["source"])
            if source is not None:
                clean_source[Path(row["image"]).name] = source

    # --- 1. genuine backs whose QR reads, keyed by the last four digits it names ---
    print("Reading the QR on every genuine Aadhaar image...")
    backs: dict[str, tuple[int, bytes]] = {}
    back_sources: set[Path] = set()
    for path in sorted((RAW / "aadhaar").iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        data = path.read_bytes()
        qr, _ = read_aadhaar_qr(data, thorough=True)
        if qr is None or not qr.last_four_digits:
            continue
        analysis = analyze_document(data, "back", registry=registry, face_provider=NullFaceProvider())
        if analysis.side.value != "back":
            continue  # a front that carries its own QR is checked on its own
        backs.setdefault(qr.last_four_digits, (len(backs) + 1, data))
        back_sources.add(path.resolve())
    print(f"  {len(backs)} genuine back(s) with a readable QR\n")

    # --- 2. clean fronts that print the same number as one of those backs ---
    pairs: list[tuple[str, int, bytes]] = []  # (clean name, back #, back bytes)
    for name, source in sorted(clean_source.items()):
        if source.resolve() in back_sources:
            continue
        clean = GENERATED / "clean" / name
        analysis = analyze_document(clean.read_bytes(), name, registry=registry, face_provider=NullFaceProvider())
        if analysis.side.value == "back" or not analysis.fields.document_number.present:
            continue
        digits = "".join(ch for ch in str(analysis.fields.document_number.value) if ch.isdigit())
        if len(digits) >= 4 and digits[-4:] in backs:
            number, data = backs[digits[-4:]]
            pairs.append((name, number, data))
    print(f"{len(pairs)} clean front(s) matched to a genuine back by number")
    if not pairs:
        print("Nothing to measure: no front in the generated set prints a number whose back QR reads.")
        return 1

    # --- 3. clean pair, then every forgery of that front with the same back ---
    per_op: dict[str, Counter] = defaultdict(Counter)
    caught_by: Counter = Counter()
    details: list[dict] = []
    started = time.perf_counter()

    for name, back_number, back in pairs:
        n = name.split("_")[1]
        baseline = verify_case(
            [((GENERATED / "clean" / name).read_bytes(), name), (back, "back.jpg")],
            registry=registry,
            face_provider=faces,
        )
        base_decision = baseline.overall_risk.decision.value
        base_adverse = adverse(baseline)
        print(f"\n  {name} + genuine back #{back_number}: clean pair -> {base_decision}")

        for forged_path in sorted((GENERATED / "tampered").glob(f"aadhaar_{n}_*.jpg")):
            match = TAMPERED_NAME.match(forged_path.name)
            if not match:
                continue
            op = match["op"]
            result = verify_case(
                [(forged_path.read_bytes(), forged_path.name), (back, "back.jpg")],
                registry=registry,
                face_provider=faces,
            )
            decision = result.overall_risk.decision.value
            new_codes = sorted(adverse(result) - base_adverse)

            stats = per_op[op]
            stats["pairs"] += 1
            stats["accepted"] += decision == "accept"
            if base_decision == "accept":
                stats["usable"] += 1
                if decision != "accept":
                    stats["caught"] += 1
                    caught_by.update(new_codes or ["(escalated, no new code)"])
            was = single_image_decision(forged_path.read_bytes(), registry, analyze_document, faces)
            stats["single_accepted"] += was == "accept"
            details.append(
                {
                    "file": forged_path.name,
                    "op": op,
                    "back": back_number,
                    "clean_pair_decision": base_decision,
                    "forged_pair_decision": decision,
                    "single_image_decision": was,
                    "new_adverse_codes": new_codes,
                }
            )
            print(f"    {op:<24} {decision:<14} {', '.join(new_codes) or '-'}")

    generated_elapsed = time.perf_counter() - started
    controlled, controlled_elapsed = (
        ([], 0.0)
        if args.no_face
        else controlled_edits(pairs, registry, verify_case, analyze_document)
    )

    totals = Counter()
    for stats in per_op.values():
        totals.update(stats)

    print("\n" + "=" * 72)
    print("FORGED FRONT + GENUINE BACK")
    print("=" * 72)
    print(f"  {'operation':<24}{'caught (of usable)':<26}{'auto-accepted':<18}{'single image'}")
    for op, s in sorted(per_op.items()):
        print(
            f"  {op:<24}{s['caught']}/{s['usable']:<24}"
            f"{s['accepted']}/{s['pairs']:<16}{s['single_accepted']}/{s['pairs']} accepted"
        )
    print(f"\n  Caught:          {totals['caught']}/{totals['usable']}  {rate(totals['caught'], totals['usable'])}")
    print(f"  Auto-accepted:   {totals['accepted']}/{totals['pairs']}  {rate(totals['accepted'], totals['pairs'])}")
    print(f"  Same forgeries, each image alone with the QR policy off: "
          f"{totals['single_accepted']}/{totals['pairs']} auto-accepted")
    if caught_by:
        print("\n  Caught by:")
        for code, count in caught_by.most_common():
            print(f"    {count:>3}  {code}")
    print(f"\n  {len(details)} forged pairs in {generated_elapsed:.0f} s")

    if controlled:
        print("\n" + "=" * 72)
        print("CONTROLLED EDITS OF THE FIELDS THE QR VOUCHES FOR")
        print("=" * 72)
        print(f"  {'edit':<16}{'took':<8}{'caught (pair)':<16}accepted as a single image, no QR policy")
        by_edit: dict[str, Counter] = defaultdict(Counter)
        for row in controlled:
            c = by_edit[row["edit"]]
            c["attempted"] += 1
            if row["took"]:
                c["took"] += 1
                usable = row["clean_pair_decision"] == "accept"
                c["usable"] += usable
                c["caught"] += usable and row["forged_pair_decision"] != "accept"
                c["single_accepted"] += row["single_image_decision"] == "accept"
        for edit, c in sorted(by_edit.items()):
            print(
                f"  {edit:<16}{c['took']}/{c['attempted']:<6}{c['caught']}/{c['usable']:<14}"
                f"{c['single_accepted']}/{c['took']}"
            )
        print()
        for row in controlled:
            if row["took"]:
                print(
                    f"    {row['edit']:<14} back #{row['back']}  "
                    f"{row['forged_pair_decision']:<14}{', '.join(row['new_adverse_codes']) or '-'}"
                )
            else:
                print(f"    {row['edit']:<14} back #{row['back']}  edit not read by the pipeline -- not counted")
        print(f"\n  {len(controlled)} controlled edits in {controlled_elapsed:.0f} s")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "pairs": len(pairs),
                    "faces": not args.no_face,
                    "per_operation": {op: dict(s) for op, s in sorted(per_op.items())},
                    "totals": dict(totals),
                    "caught_by": dict(caught_by.most_common()),
                    "details": details,
                    "controlled": controlled,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  written to {args.json}")
    return 0


def controlled_edits(pairs, registry, verify_case, analyze_document):
    """Edit the date of birth, the number and the portrait; submit each with the genuine back."""
    import cv2
    import numpy as np

    from app.pipeline.stages import ocr as ocr_stage
    from app.pipeline.stages.face import NullFaceProvider, cosine_similarity
    from app.pipeline.stages.face import get_default_provider as get_faces

    ocr, faces, no_faces = ocr_stage.get_default_provider(), get_faces(), NullFaceProvider()
    rng = random.Random(7)
    started = time.perf_counter()

    def load(data: bytes):
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def jpeg(img) -> bytes:
        return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()

    def portrait(data: bytes):
        found = faces.analyze(data).primary
        return found if found is not None and found.has_embedding else None

    # Every clean front's portrait: the donor pool for the photo swap.
    donors = []
    for path in sorted((GENERATED / "clean").glob("aadhaar_*_clean.jpg")):
        data = path.read_bytes()
        face = portrait(data)
        if face is not None:
            donors.append((path.name, load(data), face))

    rows = []
    for name, back_number, back in pairs:
        clean_bytes = (GENERATED / "clean" / name).read_bytes()
        img = load(clean_bytes)
        baseline = verify_case([(clean_bytes, name), (back, "back.jpg")], registry=registry)
        base_decision = baseline.overall_risk.decision.value
        base_adverse = adverse(baseline)
        original = analyze_document(clean_bytes, name, registry=registry, face_provider=no_faces)
        lines = ocr.read(clean_bytes).lines

        edits = []
        # --- date of birth, moved seven years ---
        dob_line = next((ln for ln in lines if DATE.search(ln.text) and ln.region), None)
        if dob_line is not None:
            d, m, y = DATE.search(dob_line.text).groups()
            edits.append(("date_of_birth", rewrite_line(img, dob_line.region, f"DOB: {d}/{m}/{int(y) - 7}")))

        # --- number, another checksum-valid one ---
        number_line = next((ln for ln in lines if NUMBER.search(ln.text) and ln.region), None)
        if number_line is not None:
            digits = re.sub(r"\D", "", NUMBER.search(number_line.text).group(0))
            edits.append(("number", rewrite_line(img, number_line.region, another_valid_number(digits, rng))))

        # --- portrait, replaced by a different person's ---
        own = portrait(clean_bytes)
        donor = None
        if own is not None:
            donor = next(
                (
                    (donor_img, face)
                    for other, donor_img, face in donors
                    if other != name and cosine_similarity(own.embedding, face.embedding) < 0.2
                ),
                None,
            )
        if donor is not None:
            donor_img, donor_face = donor
            r, dr = own.region, donor_face.region
            swapped = img.copy()
            swapped[r.y : r.y + r.height, r.x : r.x + r.width] = cv2.resize(
                donor_img[dr.y : dr.y + dr.height, dr.x : dr.x + dr.width],
                (r.width, r.height),
                interpolation=cv2.INTER_LANCZOS4,
            )
            edits.append(("portrait", swapped))

        for edit, edited in edits:
            data = jpeg(edited)
            # Did the edit take? Judged by the pipeline's own reading of it.
            if edit == "portrait":
                new_face = portrait(data)
                took = new_face is not None and cosine_similarity(own.embedding, new_face.embedding) < 0.3
            else:
                reread = analyze_document(data, "edited.jpg", registry=registry, face_provider=no_faces)
                if edit == "date_of_birth":
                    took = (
                        reread.fields.date_of_birth.present
                        and reread.fields.date_of_birth.value != original.fields.date_of_birth.value
                    )
                else:
                    before = re.sub(r"\D", "", str(original.fields.document_number.value or ""))
                    after = re.sub(r"\D", "", str(reread.fields.document_number.value or ""))
                    # A checksum-valid number, so anything that catches it is
                    # not simply the Verhoeff check.
                    took = (
                        len(after) == 12
                        and after[-4:] != before[-4:]
                        and any(s.code == "aadhaar.checksum.valid" for s in reread.signals)
                    )

            row = {
                "edit": edit,
                "back": back_number,
                "took": bool(took),
                "clean_pair_decision": base_decision,
            }
            if took:
                result = verify_case([(data, "edited.jpg"), (back, "back.jpg")], registry=registry)
                row.update(
                    forged_pair_decision=result.overall_risk.decision.value,
                    single_image_decision=single_image_decision(data, registry, analyze_document),
                    new_adverse_codes=sorted(adverse(result) - base_adverse),
                )
            rows.append(row)
    return rows, time.perf_counter() - started


if __name__ == "__main__":
    raise SystemExit(main())
