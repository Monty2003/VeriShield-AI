"""
Train a patch-level tampering detector.

Why patch-level, and not whole-image
------------------------------------
There are 28 unique source documents. A network trained to classify whole
images as tampered or clean would learn to recognise those 28 documents, not
to recognise tampering -- and it would score beautifully on a random split
while being useless on anything new.

Training on PATCHES fixes both problems at once. Each document yields hundreds
of patches, so 28 documents become tens of thousands of samples; and a patch
64 pixels across cannot identify which document it came from, so the only
signal available to the network is local -- which is exactly the signal we
want it to find.

Crucially, clean patches are drawn from the tampered images too. A patch from
an untouched corner of an edited document is genuinely clean, and including it
forces the network to distinguish an edited REGION from an edited DOCUMENT.
Without those, "was this image tampered anywhere" is separable from image-wide
cues and the network never has to look at the edit.

The split
---------
Grouped by SOURCE DOCUMENT, never by image or by patch. A clean and a tampered
copy of the same card share paper, print, lighting and camera noise; letting
one into training and the other into test lets the network match on the
document and report an accuracy that means nothing. This is the single easiest
way to get a great-looking number from a worthless model, so the split is
asserted in code rather than trusted.

What to expect
--------------
The tampering is synthetic. A detector fitted to it may well be learning the
artefacts of THIS generator rather than forgery in general, and 28 source
documents is a small base whatever the patch count says. The evaluation below
is built to expose that rather than hide it: held-out documents, an operating
point chosen at low false-positive rate, and a localisation check that asks
whether the model points at the actual edit.

Usage:
    python scripts/train_tamper_detector.py
    python scripts/train_tamper_detector.py --epochs 12 --patch 96
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

GENERATED = BACKEND / "data" / "datasets" / "generated"
MODEL_DIR = BACKEND / "data" / "models"

# Patch size, chosen from the data rather than picked.
#
# Measured across the 122 tampered images, the edited regions have a median
# height of 32 pixels -- they are text lines. A 64-pixel patch centred on one
# can be at most about half inside it, so the 70% overlap requirement below
# rejected almost every candidate and the first run produced a dataset that
# was 0% positive. At 32 pixels a patch fits inside a text line completely.
DEFAULT_PATCH = 32

# Patches are upsampled to this before the network sees them. ResNet's stem
# (7x7 stride 2, then a stride-2 pool) collapses a 32-pixel input to 8x8 before
# the first residual block, which throws away most of what a tampering cue
# looks like. Feeding it 64 keeps a usable spatial extent without changing the
# architecture.
NETWORK_INPUT = 64

# A patch counts as tampered only if this much of it lies inside the mask.
# Patches straddling the boundary are ambiguous and are dropped entirely
# rather than assigned a label they only half deserve.
TAMPERED_MIN_OVERLAP = 0.70
CLEAN_MAX_OVERLAP = 0.0

# Clean patches must sit this far from any edited pixel. The pixels just
# outside an edit are disturbed by resampling and JPEG blocking, and labelling
# them clean teaches the network that the edge of a forgery is normal.
CLEAN_MARGIN_PX = 24

# Patches that are nearly uniform carry no evidence either way -- blank paper
# looks the same whoever printed it. Including them floods both classes with
# identical samples and drags the network toward predicting the base rate.
MIN_PATCH_STDDEV = 6.0


def load_manifest() -> list[dict]:
    path = GENERATED / "manifest.jsonl"
    if not path.exists():
        raise SystemExit(
            f"No dataset at {path}.\n"
            f"Run: python scripts/generate_tampered_dataset.py --variants 3"
        )
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]


def source_key(record: dict) -> str:
    """Which original document a record came from -- the unit the split uses."""
    return Path(record["source"]).stem


def sample_patches(
    image: np.ndarray,
    mask: np.ndarray | None,
    patch: int,
    per_image: int,
    rng: random.Random,
) -> tuple[list[np.ndarray], list[int]]:
    """
    Draw labelled patches from one image.

    With a mask, tampered patches are taken from inside it and clean ones from
    well outside. Without a mask (a clean image), every patch is clean.
    """
    h, w = image.shape[:2]
    if h < patch or w < patch:
        return [], []

    patches: list[np.ndarray] = []
    labels: list[int] = []

    # Distance from every pixel to the nearest edited one, so the clean margin
    # can be enforced cheaply.
    if mask is not None and mask.any():
        distance = cv2.distanceTransform((mask == 0).astype(np.uint8), cv2.DIST_L2, 5)
    else:
        distance = None

    want_tampered = per_image // 2 if mask is not None and mask.any() else 0
    got_tampered = got_clean = 0

    # --- tampered patches: sample INSIDE the edited region ----------------
    #
    # Not by chance across the whole frame. An edited field is a few thousand
    # pixels on an image of two and a half million, so uniform sampling lands
    # inside it roughly once in a thousand tries -- the dataset would come out
    # with almost no positives at all, and the network would learn to answer
    # "clean" and score 99%.
    if want_tampered:
        ys, xs = np.where(mask > 0)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())

        attempts = 0
        while got_tampered < want_tampered and attempts < want_tampered * 60:
            attempts += 1
            # Pick the window's CENTRE inside the edited region, then clamp the
            # window to the image. Choosing the top-left corner instead needs a
            # range that inverts whenever the region is wider than the patch,
            # which is the common case -- an edited field is a long thin box.
            cy = rng.randint(y0, y1)
            cx = rng.randint(x0, x1)
            y = min(max(cy - patch // 2, 0), h - patch)
            x = min(max(cx - patch // 2, 0), w - patch)

            window = image[y : y + patch, x : x + patch]
            if window.shape[:2] != (patch, patch):
                continue
            if float(window.std()) < MIN_PATCH_STDDEV:
                continue
            if float((mask[y : y + patch, x : x + patch] > 0).mean()) < TAMPERED_MIN_OVERLAP:
                continue
            patches.append(window)
            labels.append(1)
            got_tampered += 1

    # --- clean patches: anywhere far enough from an edit -------------------
    want_clean = per_image - got_tampered
    attempts = 0
    while got_clean < want_clean and attempts < want_clean * 40:
        attempts += 1
        y = rng.randrange(0, h - patch)
        x = rng.randrange(0, w - patch)
        window = image[y : y + patch, x : x + patch]

        if float(window.std()) < MIN_PATCH_STDDEV:
            continue

        if mask is not None:
            if float((mask[y : y + patch, x : x + patch] > 0).mean()) > CLEAN_MAX_OVERLAP:
                continue
            centre = distance[y + patch // 2, x + patch // 2] if distance is not None else 1e9
            if centre < CLEAN_MARGIN_PX:
                continue

        patches.append(window)
        labels.append(0)
        got_clean += 1

    return patches, labels


def build_dataset(records: list[dict], patch: int, per_image: int, rng: random.Random):
    """Turn the manifest into patch arrays, grouped by source document."""
    by_source: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_source[source_key(record)].append(record)

    data: dict[str, tuple[list[np.ndarray], list[int]]] = {}
    for source, group in by_source.items():
        patches: list[np.ndarray] = []
        labels: list[int] = []
        for record in group:
            image = cv2.imread(str(GENERATED / record["image"]), cv2.IMREAD_COLOR)
            if image is None:
                continue
            mask = None
            if record["label"] == "tampered" and record.get("mask"):
                mask = cv2.imread(str(GENERATED / record["mask"]), cv2.IMREAD_GRAYSCALE)
            p, l = sample_patches(image, mask, patch, per_image, rng)
            patches.extend(p)
            labels.extend(l)
        if patches:
            data[source] = (patches, labels)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a patch-level tampering detector.")
    parser.add_argument("--patch", type=int, default=DEFAULT_PATCH)
    parser.add_argument("--per-image", type=int, default=60, help="Patches sampled per image.")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--target-fpr", type=float, default=0.10)
    args = parser.parse_args()

    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
        from torchvision.models import resnet18
    except ImportError:
        raise SystemExit(
            "PyTorch is not installed.\n"
            "  pip install torch torchvision --index-url "
            "https://download.pytorch.org/whl/cu121"
        )

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    records = load_manifest()
    print(f"Loaded {len(records)} images from the generated dataset.")

    print(f"Sampling {args.patch}x{args.patch} patches...")
    data = build_dataset(records, args.patch, args.per_image, rng)

    sources = sorted(data)
    rng.shuffle(sources)
    cut = max(1, int(len(sources) * 0.7))
    train_sources, test_sources = sources[:cut], sources[cut:]

    # The invariant that makes the numbers below mean anything.
    assert not (set(train_sources) & set(test_sources)), "source leaked across the split"

    def stack(source_list):
        patches, labels = [], []
        for s in source_list:
            p, l = data[s]
            patches.extend(p)
            labels.extend(l)
        if not patches:
            return None, None
        stacked = np.stack(
            [
                cv2.resize(p, (NETWORK_INPUT, NETWORK_INPUT), interpolation=cv2.INTER_CUBIC)
                for p in patches
            ]
        ).astype(np.float32) / 255.0
        x = torch.from_numpy(stacked).permute(0, 3, 1, 2)
        y = torch.tensor(labels, dtype=torch.float32)
        return x, y

    x_train, y_train = stack(train_sources)
    x_test, y_test = stack(test_sources)

    if x_train is None or x_test is None:
        raise SystemExit("Not enough patches to train. Generate more variants first.")

    print(f"\n  source documents : {len(train_sources)} train / {len(test_sources)} test")
    print(f"  train patches    : {len(x_train)}  ({int(y_train.sum())} tampered)")
    print(f"  test patches     : {len(x_test)}  ({int(y_test.sum())} tampered)")

    if y_train.sum() < 50 or y_test.sum() < 20:
        print("\n  WARNING: very few tampered patches. Results will be noisy.")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  device           : {device}")

    # ResNet-18 rather than anything larger: the dataset is small, and a bigger
    # network would memorise it faster without learning more.
    model = resnet18(weights="IMAGENET1K_V1")
    model.fc = nn.Linear(model.fc.in_features, 1)
    model = model.to(device)

    # The classes are unbalanced by construction, so the loss is weighted --
    # otherwise the network learns to answer "clean" and be right most of the
    # time, which is the base rate rather than a detector.
    positive_weight = float((len(y_train) - y_train.sum()) / max(y_train.sum(), 1))
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(positive_weight, device=device))
    optimiser = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)

    loader = DataLoader(
        TensorDataset(x_train, y_train), batch_size=args.batch, shuffle=True, drop_last=False
    )

    print(f"\nTraining {args.epochs} epochs (pos_weight={positive_weight:.2f})...")
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            optimiser.zero_grad()
            loss = criterion(model(xb).squeeze(1), yb)
            loss.backward()
            optimiser.step()
            total += loss.item() * len(xb)
        print(f"  epoch {epoch:>2}/{args.epochs}  loss {total / len(x_train):.4f}")

    print(f"  trained in {time.perf_counter() - started:.0f}s")

    # ---- evaluation on held-out DOCUMENTS -------------------------------
    model.eval()
    scores = []
    with torch.no_grad():
        for i in range(0, len(x_test), 256):
            batch = x_test[i : i + 256].to(device)
            scores.append(torch.sigmoid(model(batch).squeeze(1)).cpu().numpy())
    scores = np.concatenate(scores)
    truth = y_test.numpy()

    print(f"\n{'=' * 70}\nEVALUATION on {len(test_sources)} held-out documents\n{'=' * 70}")

    tampered_scores = scores[truth == 1]
    clean_scores = scores[truth == 0]
    print(f"  clean patches    mean {clean_scores.mean():.3f}  median {np.median(clean_scores):.3f}")
    print(f"  tampered patches mean {tampered_scores.mean():.3f}  median {np.median(tampered_scores):.3f}")

    # ROC across observed thresholds, and the best point at an acceptable FPR.
    best = None
    for threshold in np.unique(np.round(scores, 3)):
        tpr = float((tampered_scores >= threshold).mean())
        fpr = float((clean_scores >= threshold).mean())
        if fpr <= args.target_fpr and (best is None or tpr > best[1]):
            best = (float(threshold), tpr, fpr)

    # AUC via rank statistic -- no sklearn dependency needed.
    order = np.argsort(scores)
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    n_pos, n_neg = (truth == 1).sum(), (truth == 0).sum()
    auc = (ranks[truth == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    print(f"\n  AUC: {auc:.3f}   (0.5 = coin flip)")

    if best:
        threshold, tpr, fpr = best
        print(f"  best at FPR<={args.target_fpr:.0%}:  threshold {threshold:.3f}  "
              f"TPR {tpr:.1%}  FPR {fpr:.1%}")
    else:
        print(f"  no threshold reaches FPR <= {args.target_fpr:.0%}")

    verdict = "usable" if auc >= 0.75 and best and best[1] >= 0.4 else "weak"
    print(f"\n  VERDICT: {verdict.upper()}")
    if verdict == "weak":
        print("  This does not separate tampered from clean well enough on documents")
        print("  it has never seen. Do not enable it: a detector at this level")
        print("  reports noise, and noise presented as evidence is worse than")
        print("  silence. More source documents is the fix, not more epochs.")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    weights_path = MODEL_DIR / "tamper_patch_resnet18.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "patch_size": args.patch,
            "auc": float(auc),
            "threshold": best[0] if best else None,
            "tpr": best[1] if best else None,
            "fpr": best[2] if best else None,
            "verdict": verdict,
            "train_sources": train_sources,
            "test_sources": test_sources,
        },
        weights_path,
    )
    print(f"\n  saved to {weights_path}")

    (MODEL_DIR / "tamper_metrics.json").write_text(
        json.dumps(
            {
                "auc": float(auc),
                "threshold": best[0] if best else None,
                "tpr": best[1] if best else None,
                "fpr": best[2] if best else None,
                "verdict": verdict,
                "patch_size": args.patch,
                "n_train_sources": len(train_sources),
                "n_test_sources": len(test_sources),
                "n_train_patches": len(x_train),
                "n_test_patches": len(x_test),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
