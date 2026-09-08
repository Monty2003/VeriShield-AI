"""
Generate a labelled tampered-document dataset from clean document images.

Why this exists
---------------
The classical forensic detectors in this project (ELA, copy-move) are
currently disabled because every threshold in them is a guess -- there is no
labelled data to measure them against. See docs/FORENSICS.md.

This script bootstraps that data. It takes clean document photographs and
produces tampered variants together with PIXEL-LEVEL GROUND TRUTH MASKS, which
turns detector calibration from guesswork into measurement: you can compute a
true/false positive rate at a chosen threshold instead of eyeballing it.

Honest limitation, stated up front
----------------------------------
Synthetic tampering is NOT drawn from the same distribution as real forgery.
A detector calibrated only on this will be optimistic about its real-world
performance. This is a bootstrap, not a substitute for real tampered documents
-- but it is far better than the zero labelled examples we have now, and it is
the standard way to get a first usable threshold.

The tampering is applied to REAL text regions found in the document, not to
random rectangles, because where an edit lands changes how detectable it is:
a forger alters a date or a name, and those areas have their own texture and
compression behaviour.

Usage
-----
    python scripts/generate_tampered_dataset.py
    python scripts/generate_tampered_dataset.py --variants 3 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

DATASETS = BACKEND / "data" / "datasets"
RAW_DIR = DATASETS / "raw"
OUT_DIR = DATASETS / "generated"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# Quality the "clean" reference copies are written at. Every clean and
# tampered image is encoded at this SAME quality so that a detector cannot
# separate the two classes by overall file quality instead of by the edit --
# which would produce a detector that scores well here and fails completely
# on real documents.
BASE_QUALITY = 92

# Quality a spliced patch is round-tripped through before being pasted back.
# This is what gives the patch a different compression history from the page
# around it -- the signal ELA is designed to find.
SPLICE_QUALITY = 60

# Phone photographs arrive around 3000x4000. Everything is normalised to this
# long edge first, for two reasons: a full-size frame makes an edited field a
# far smaller fraction of the image than any real forgery would be, and the
# clean and tampered copies must be identical in every respect except the edit
# -- including size -- or a detector can separate the classes without ever
# looking at the tampering.
MAX_WORKING_EDGE = 1600


@dataclass
class TamperResult:
    """One tampered image plus its ground truth."""

    image: np.ndarray
    mask: np.ndarray  # uint8, 255 where tampered
    operation: str
    bbox: tuple[int, int, int, int]
    note: str = ""


@dataclass
class Stats:
    sources: int = 0
    clean_written: int = 0
    tampered_written: int = 0
    skipped: list[str] = field(default_factory=list)
    by_operation: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Region finding -- no OCR required
# --------------------------------------------------------------------------


def find_text_regions(
    img: np.ndarray, min_width: int = 40, min_height: int = 10
) -> list[tuple[int, int, int, int]]:
    """
    Locate text-line regions using morphology alone.

    Deliberately not OCR: this must run before the ML stack is installed, and
    it only needs to know WHERE text is, not what it says. A morphological
    gradient followed by a wide horizontal close merges characters into lines,
    which is exactly the granularity a forger edits at -- one field, one line.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Morphological gradient highlights character strokes.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    gradient = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel)

    _, binary = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # Wide, short kernel: joins characters along a line without merging
    # separate lines together.
    line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (18, 3))
    connected = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, line_kernel)

    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    h, w = gray.shape
    regions: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw < min_width or ch < min_height:
            continue
        # Text lines are wider than they are tall. Anything squarer is more
        # likely a photo, a logo or a border.
        if cw / ch < 1.8:
            continue
        # Reject anything spanning nearly the whole page -- that is the border.
        if cw > 0.95 * w or ch > 0.3 * h:
            continue

        # --- keep DATA FIELDS, drop printed branding ---------------------
        # A forger edits a date of birth or a document number; nobody forges
        # the words "REPUBLIC OF INDIA". Ranking regions by area alone puts
        # the masthead first, because banner titles are the largest, highest
        # contrast text on the page -- and tampering applied there produces
        # calibration data that does not resemble real forgery.
        #
        # Two cheap discriminators separate the two reliably:
        #   * Header band. Mastheads sit in the top strip of the document.
        #   * Ink polarity. Data fields are dark ink on light paper; banner
        #     text is light ink on a dark bar, so its region is darker than
        #     the page median.
        if y < 0.12 * h:
            continue

        region_pixels = gray[y : y + ch, x : x + cw]
        if region_pixels.size == 0:
            continue
        if float(np.median(region_pixels)) < 0.6 * float(np.median(gray)):
            continue

        regions.append((x, y, cw, ch))

    # Sorted by area so callers can bias toward substantial fields, but the
    # caller samples across the whole list rather than only the largest --
    # picking only the biggest region repeatedly would tamper the same field
    # on every document and teach a detector that one location.
    regions.sort(key=lambda r: r[2] * r[3], reverse=True)
    return regions


def find_portrait_region(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    Locate the portrait photograph, if there is one.

    Uses OpenCV's bundled Haar cascade. It is old and imperfect, but it ships
    with the package, needs no download, and is entirely adequate for finding
    a large front-facing portrait on an ID document.
    """
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if cascade.empty():
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        return None

    # Largest detection: the document portrait, not a face inside a watermark.
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])

    # Expand to the printed photo box, which extends beyond the face itself.
    pad_x, pad_y = int(w * 0.35), int(h * 0.45)
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(img.shape[1], x + w + pad_x)
    y1 = min(img.shape[0], y + h + pad_y)
    return x0, y0, x1 - x0, y1 - y0


def _recompress(patch: np.ndarray, quality: int) -> np.ndarray:
    """Round-trip a patch through JPEG to give it its own compression history."""
    ok, encoded = cv2.imencode(".jpg", patch, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return patch
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def _mask_for(shape: tuple[int, ...], bbox: tuple[int, int, int, int]) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    x, y, w, h = bbox
    mask[y : y + h, x : x + w] = 255
    return mask


# --------------------------------------------------------------------------
# Tamper operations
# --------------------------------------------------------------------------


def tamper_text_splice(
    img: np.ndarray, regions: list[tuple], rng: random.Random
) -> TamperResult | None:
    """
    Replace a text field with the same field re-encoded at a different quality.

    Models the most common real edit: the forger retypes a value in an editor
    and saves. The pixels look plausible but carry a compression history that
    does not match the rest of the page.
    """
    if not regions:
        return None

    x, y, w, h = rng.choice(regions)
    out = img.copy()
    patch = _recompress(img[y : y + h, x : x + w], SPLICE_QUALITY)
    out[y : y + h, x : x + w] = patch

    return TamperResult(
        image=out,
        mask=_mask_for(img.shape, (x, y, w, h)),
        operation="text_splice",
        bbox=(x, y, w, h),
        note=f"text region re-encoded at q{SPLICE_QUALITY} and pasted back",
    )


def tamper_copy_move(
    img: np.ndarray, regions: list[tuple], rng: random.Random
) -> TamperResult | None:
    """
    Clone a background patch over a text field.

    Models erasure: covering a value with nearby blank paper. It preserves the
    surrounding texture perfectly, which is exactly why it is hard to detect
    and why forgers use it.
    """
    if not regions:
        return None

    x, y, w, h = rng.choice(regions)
    ih, iw = img.shape[:2]

    # Find a source patch of the same size that is mostly blank and does not
    # overlap the destination.
    best: tuple[float, int, int] | None = None
    for _ in range(60):
        sx = rng.randint(0, max(0, iw - w - 1))
        sy = rng.randint(0, max(0, ih - h - 1))
        if abs(sx - x) < w and abs(sy - y) < h:
            continue
        candidate = img[sy : sy + h, sx : sx + w]
        if candidate.shape[:2] != (h, w):
            continue
        # Low variance == blank paper, which is what an eraser wants.
        variance = float(candidate.std())
        if best is None or variance < best[0]:
            best = (variance, sx, sy)

    if best is None:
        return None

    _, sx, sy = best
    out = img.copy()
    out[y : y + h, x : x + w] = img[sy : sy + h, sx : sx + w]

    mask = _mask_for(img.shape, (x, y, w, h))
    return TamperResult(
        image=out,
        mask=mask,
        operation="copy_move",
        bbox=(x, y, w, h),
        note=f"patch cloned from ({sx},{sy}) over field at ({x},{y})",
    )


def tamper_photo_substitution(
    img: np.ndarray, donor: np.ndarray | None, rng: random.Random
) -> TamperResult | None:
    """
    Replace the portrait with another document's portrait.

    The highest-stakes edit there is: the document keeps a real identity's
    details but shows someone else's face. Requires a donor image that also
    has a detectable portrait.
    """
    target_box = find_portrait_region(img)
    if target_box is None or donor is None:
        return None

    donor_box = find_portrait_region(donor)
    if donor_box is None:
        return None

    x, y, w, h = target_box
    dx, dy, dw, dh = donor_box

    donor_face = donor[dy : dy + dh, dx : dx + dw]
    if donor_face.size == 0:
        return None

    resized = cv2.resize(donor_face, (w, h), interpolation=cv2.INTER_LANCZOS4)
    out = img.copy()
    out[y : y + h, x : x + w] = resized

    return TamperResult(
        image=out,
        mask=_mask_for(img.shape, target_box),
        operation="photo_substitution",
        bbox=target_box,
        note="portrait replaced with a portrait from another document",
    )


def tamper_inpaint_overwrite(
    img: np.ndarray, regions: list[tuple], rng: random.Random
) -> TamperResult | None:
    """
    Smooth a field away and write a new value over it.

    Models the "clean then retype" edit. The smoothing destroys the local
    noise floor, which is the signal the noise-consistency detector looks for.
    """
    if not regions:
        return None

    x, y, w, h = rng.choice(regions)
    out = img.copy()

    # Erase by inpainting from the surrounding pixels.
    region_mask = np.zeros(img.shape[:2], dtype=np.uint8)
    region_mask[y : y + h, x : x + w] = 255
    out = cv2.inpaint(out, region_mask, 3, cv2.INPAINT_TELEA)

    # Write a plausible replacement value into the cleared space.
    replacement = rng.choice(
        ["12/04/1996", "01/01/1990", "RAJESH KUMAR", "A1234567", "24/11/2031"]
    )
    scale = max(0.4, min(1.4, h / 32.0))
    cv2.putText(
        out,
        replacement,
        (x + 2, y + h - max(2, int(h * 0.22))),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (30, 30, 30),
        max(1, int(scale * 2)),
        cv2.LINE_AA,
    )

    return TamperResult(
        image=out,
        mask=_mask_for(img.shape, (x, y, w, h)),
        operation="inpaint_overwrite",
        bbox=(x, y, w, h),
        note=f"field inpainted away and overwritten with {replacement!r}",
    )


def tamper_cross_document_splice(
    img: np.ndarray, regions: list[tuple], rng: random.Random, donor: np.ndarray | None = None
) -> TamperResult | None:
    """
    Paste a text region taken from a DIFFERENT document.

    This is what a real forgery does, and it differs from the same-image splice
    in the way that matters most to compression forensics: the pasted pixels
    carry another camera's sensor noise and another file's quantisation
    history, not a second copy of this one's.

    The distinction is not academic. Re-encoding a patch from the same photo
    and pasting it back leaves both halves sharing an origin, and the final
    save flattens what little difference remained -- which is precisely why the
    first calibration run measured no separation at all. If these detectors can
    see anything, they should see this.
    """
    if not regions or donor is None:
        return None

    x, y, w, h = rng.choice(regions)
    donor_regions = find_text_regions(donor)
    if not donor_regions:
        return None

    # Prefer a donor region of similar shape so the paste is not obvious to the
    # eye -- a forger would choose one too.
    donor_regions.sort(key=lambda r: abs(r[2] / max(r[3], 1) - w / max(h, 1)))
    dx, dy, dw, dh = donor_regions[0]

    patch = donor[dy : dy + dh, dx : dx + dw]
    if patch.size == 0:
        return None

    out = img.copy()
    out[y : y + h, x : x + w] = cv2.resize(patch, (w, h), interpolation=cv2.INTER_LANCZOS4)

    return TamperResult(
        image=out,
        mask=_mask_for(img.shape, (x, y, w, h)),
        operation="cross_document_splice",
        bbox=(x, y, w, h),
        note="text region transplanted from a different document",
    )


OPERATIONS = (
    tamper_text_splice,
    tamper_copy_move,
    tamper_inpaint_overwrite,
)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def collect_sources() -> list[tuple[Path, str]]:
    """Find every clean image under raw/, tagged with its document type."""
    found: list[tuple[Path, str]] = []
    if not RAW_DIR.exists():
        return found
    for type_dir in sorted(RAW_DIR.iterdir()):
        if not type_dir.is_dir():
            continue
        for path in sorted(type_dir.iterdir()):
            if path.suffix in IMAGE_SUFFIXES:
                found.append((path, type_dir.name))
    return found


def generate(variants: int, seed: int, quiet: bool = False) -> Stats:
    rng = random.Random(seed)
    stats = Stats()

    for sub in ("clean", "tampered", "masks"):
        (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)

    sources = collect_sources()
    stats.sources = len(sources)
    if not sources:
        return stats

    # Donor pool for portrait substitution: any other source image.
    manifest_path = OUT_DIR / "manifest.jsonl"
    records: list[dict] = []

    from app.core.imaging import decode_image

    for index, (path, doc_type) in enumerate(sources):
        try:
            img = decode_image(path.read_bytes())
        except Exception as exc:  # noqa: BLE001 -- one bad file must not stop the run
            stats.skipped.append(f"{path.name}: could not be decoded ({exc})")
            continue

        # Phone captures are ~3000x4000. Working at full size makes tampering
        # slow and, more importantly, makes an edited field a vanishingly small
        # fraction of the frame -- far smaller than any real forgery, which
        # would understate what the detectors can find. Downscale to a size a
        # document scan realistically arrives at.
        if max(img.shape[:2]) > MAX_WORKING_EDGE:
            scale = MAX_WORKING_EDGE / max(img.shape[:2])
            img = cv2.resize(
                img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )

        stem = f"{doc_type}_{index:03d}"

        # Write the clean reference at BASE_QUALITY. Both classes go through
        # the same encoder so that overall quality carries no class signal.
        clean_name = f"{stem}_clean.jpg"
        cv2.imwrite(
            str(OUT_DIR / "clean" / clean_name),
            img,
            [cv2.IMWRITE_JPEG_QUALITY, BASE_QUALITY],
        )
        stats.clean_written += 1
        records.append(
            {
                "image": f"clean/{clean_name}",
                "source": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "doc_type": doc_type,
                "label": "clean",
                "operation": None,
                "mask": None,
                "bbox": None,
            }
        )

        regions = find_text_regions(img)
        if not regions:
            stats.skipped.append(
                f"{path.name}: no text regions found (image may be too small, "
                f"blurred, or not a document)"
            )
            continue

        # Portrait substitution needs a donor with a detectable face.
        donor = None
        if len(sources) > 1:
            donor_path = sources[(index + 1) % len(sources)][0]
            try:
                donor = decode_image(donor_path.read_bytes())
            except Exception:  # noqa: BLE001 -- a missing donor just skips that op
                donor = None

        operations = list(OPERATIONS)
        rng.shuffle(operations)
        chosen = operations[:variants]

        produced = 0
        for op in chosen:
            result = op(img, regions, rng)
            if result is None:
                continue
            produced += 1
            name = f"{stem}_{result.operation}.jpg"
            mask_name = f"{stem}_{result.operation}_mask.png"

            cv2.imwrite(
                str(OUT_DIR / "tampered" / name),
                result.image,
                [cv2.IMWRITE_JPEG_QUALITY, BASE_QUALITY],
            )
            cv2.imwrite(str(OUT_DIR / "masks" / mask_name), result.mask)

            stats.tampered_written += 1
            stats.by_operation[result.operation] = (
                stats.by_operation.get(result.operation, 0) + 1
            )
            records.append(
                {
                    "image": f"tampered/{name}",
                    "source": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    "doc_type": doc_type,
                    "label": "tampered",
                    "operation": result.operation,
                    "mask": f"masks/{mask_name}",
                    "bbox": [int(v) for v in result.bbox],
                    "note": result.note,
                }
            )

        # Cross-document splice needs a donor image, so it runs here rather
        # than in the donor-free OPERATIONS loop.
        if donor is not None:
            if max(donor.shape[:2]) > MAX_WORKING_EDGE:
                ds = MAX_WORKING_EDGE / max(donor.shape[:2])
                donor = cv2.resize(donor, None, fx=ds, fy=ds, interpolation=cv2.INTER_AREA)
            cross = tamper_cross_document_splice(img, regions, rng, donor)
            if cross is not None:
                name = f"{stem}_cross_document_splice.jpg"
                mask_name = f"{stem}_cross_document_splice_mask.png"
                cv2.imwrite(str(OUT_DIR / "tampered" / name), cross.image,
                            [cv2.IMWRITE_JPEG_QUALITY, BASE_QUALITY])
                cv2.imwrite(str(OUT_DIR / "masks" / mask_name), cross.mask)
                stats.tampered_written += 1
                stats.by_operation["cross_document_splice"] = (
                    stats.by_operation.get("cross_document_splice", 0) + 1
                )
                records.append({
                    "image": f"tampered/{name}",
                    "source": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    "doc_type": doc_type,
                    "label": "tampered",
                    "operation": "cross_document_splice",
                    "mask": f"masks/{mask_name}",
                    "bbox": [int(v) for v in cross.bbox],
                    "note": cross.note,
                })

        # Portrait substitution is attempted separately since it needs a donor
        # rather than a text region.
        substitution = tamper_photo_substitution(img, donor, rng)
        if substitution is not None:
            name = f"{stem}_photo_substitution.jpg"
            mask_name = f"{stem}_photo_substitution_mask.png"
            cv2.imwrite(
                str(OUT_DIR / "tampered" / name),
                substitution.image,
                [cv2.IMWRITE_JPEG_QUALITY, BASE_QUALITY],
            )
            cv2.imwrite(str(OUT_DIR / "masks" / mask_name), substitution.mask)
            stats.tampered_written += 1
            stats.by_operation["photo_substitution"] = (
                stats.by_operation.get("photo_substitution", 0) + 1
            )
            records.append(
                {
                    "image": f"tampered/{name}",
                    "source": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    "doc_type": doc_type,
                    "label": "tampered",
                    "operation": "photo_substitution",
                    "mask": f"masks/{mask_name}",
                    "bbox": [int(v) for v in substitution.bbox],
                    "note": substitution.note,
                }
            )

        if produced == 0:
            stats.skipped.append(f"{path.name}: every tamper operation declined")

    with manifest_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a labelled tampered-document dataset from clean images."
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=2,
        help="Text-based tamper variants per source image (default: 2).",
    )
    parser.add_argument(
        "--seed", type=int, default=1337, help="RNG seed, for reproducibility."
    )
    args = parser.parse_args()

    stats = generate(args.variants, args.seed)

    if stats.sources == 0:
        print("No source images found.\n")
        print(f"Put clean document images in:\n  {RAW_DIR}")
        print("\nOne subfolder per document type, for example:")
        print(f"  {RAW_DIR / 'passport'}/my_passport.jpg")
        print("\nSee backend/data/datasets/README.md for what makes a good input.")
        return 1

    print(f"Sources found      : {stats.sources}")
    print(f"Clean written      : {stats.clean_written}")
    print(f"Tampered written   : {stats.tampered_written}")
    if stats.by_operation:
        print("\nBy operation:")
        for op, count in sorted(stats.by_operation.items()):
            print(f"  {op:<22} {count}")
    if stats.skipped:
        print(f"\nSkipped ({len(stats.skipped)}):")
        for reason in stats.skipped[:10]:
            print(f"  - {reason}")

    print(f"\nOutput: {OUT_DIR}")
    print(f"Manifest: {OUT_DIR / 'manifest.jsonl'}")

    if stats.tampered_written == 0:
        print(
            "\nNothing tampered was produced. The usual cause is that no text "
            "regions were detected -- check that the images are real document "
            "photographs, in focus, and around 1000px or more on the long edge."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
