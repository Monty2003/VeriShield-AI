# Layer 5 — Forensics: measured status

This document used to say the forensic detectors were disabled pending
calibration. Calibration has now been done, against real data. The answer did
not change, but it is no longer a guess.

## What ships enabled

| Detector | Status | Basis |
|---|---|---|
| EXIF / metadata | **Enabled** | Deterministic. Reads what the file states; makes no inference. |
| Noise consistency | **Disabled** | Measured: TPR 8.2% at FPR 7.1%. Chance. |
| Error Level Analysis | **Disabled** | Not applicable to most inputs; too few samples to score. |
| Copy-move | **Disabled** | Measured: TPR 4.9% at FPR 7.1%. Chance. |
| **Learned patch model** | **Review aid only** | Strong per patch (AUC 0.80), unusable per document (TPR 39% / FPR 22%). Scores nothing. |

## The learned detector

A ResNet-18 patch classifier was trained on the generated dataset
(`scripts/train_tamper_detector.py`). It is the only component in this project
that was actually trained, and its result is split: genuinely good at one task,
unusable at another.

**Design.** Patch-level, not whole-image. There are 28 source documents; a
network classifying whole images would learn to recognise those 28 documents
and score beautifully on a random split while being useless on anything new.
Patches of 32 pixels -- the measured median height of an edited field -- cannot
identify their document, so the only signal available is local. Clean patches
are drawn from the tampered images too, which forces the network to separate an
edited REGION from an edited DOCUMENT.

The split is grouped by source document and verified, not assumed: a perceptual
hash across all 28 raw captures confirmed no physical card appears on both
sides.

**Per patch, on 9 documents it never saw:**

```
AUC 0.802          (classical detectors: ~0.50)
clean patches      median 0.021
tampered patches   median 0.794
localisation       50% when it fires   (classical: 2-8%)
```

**Per document, it does not work:**

```
threshold/cluster   TPR    FPR
0.85 /  4          100%   100%
0.90 / 32           66%    56%
0.95 / 32           58%    44%
0.99 / 32           39%    22%   <- best
0.99 / 64           11%    11%
```

True and false positive rates move together at every operating point. The best
one flags 39% of tampered documents and 22% of genuine ones -- roughly one real
document in five.

This is the multiple-comparisons problem rather than a broken model. A 10%
per-patch false-positive rate is unremarkable for one patch and overwhelming
across the two thousand patches a document contains.

**Disposition.** It never contributes to the risk score. It is exposed as a
reviewer overlay: when someone is already examining a document, the heatmap
points at the actual edit about half the time, which is worth having. Screening
with it would refuse real people at a rate no verification system should accept.

Retrain with more source documents to change this; more epochs will not.

Enable either with `run_forensics(..., enable_ela=True, enable_copy_move=True)`.

## The measurement

**Data.** 28 clean document photographs taken on a Samsung Galaxy M34 5G at
3060×4080, EXIF intact, single compression history — the properties these
methods actually depend on. From them, 122 tampered variants with pixel-level
ground-truth masks (`scripts/generate_tampered_dataset.py`), across five
operations: same-image text splice, cross-document splice, copy-move, inpaint
and overwrite, and portrait substitution.

Clean and tampered copies are written at the same JPEG quality and the same
size, so nothing but the edit distinguishes the two classes. Without that a
detector separates them on file properties and scores well while learning
nothing about tampering.

**Result** (`scripts/calibrate_forensics.py`):

```
Noise consistency
  clean     n=28   median 3.846   p95 5.859
  tampered  n=122  median 3.874   p95 6.316
  best at FPR<=10%:  TPR  8.2%    localisation  1/63  (2%)

Copy-move
  clean     n=28   mean 0.786
  tampered  n=122  mean 0.705     <- lower than clean
  best at FPR<=10%:  TPR  4.9%    localisation  3/37  (8%)
```

The distributions sit on top of each other. Tampered copy-move scores are
*lower* than clean ones. And localisation is the finding that settles it: on
the rare occasions a detector fires on a tampered image, it points at the
edited region 2–8% of the time. A confident box over the wrong field is worse
than no box, because the whole purpose is to tell a reviewer where to look.

**Error Level Analysis** could not be scored at all: it was applicable to only
9 of 28 clean images. Its own guard — that an image recompressing almost
losslessly has no compression history to analyse — correctly declines most of
these captures.

## The alternative explanation, tested

The obvious objection is that the synthetic tampering was too weak rather than
the detectors too blind. The first generator spliced a patch re-encoded from
the *same* photograph, so both halves shared an origin and the final save
flattened what little difference remained.

So a `cross_document_splice` operation was added, transplanting a text region
from a *different* document — different camera noise, different quantisation
history. That is what a real forgery does, and it is the case these methods
are designed for.

It changed nothing. Noise TPR moved 8.3% → 8.2%; copy-move moved 7.1% → 4.9%.
The hypothesis was tested and rejected.

## What this means

Classical compression and noise forensics, as implemented here, do not
discriminate tampering in photographs of identity documents. Two plausible
reasons, and this data cannot separate them:

1. A phone photograph of a card is dominated by print texture, paper grain,
   glare and lens blur. The sensor-noise and compression signals these methods
   read are small next to that.
2. Genuine forgeries in the wild may differ from synthetic ones in ways this
   bootstrap does not capture — a real forger works from a scan, edits in a
   real editor, and prints and rephotographs the result.

Resolving that requires **real tampered documents**, which no amount of
synthesis substitutes for.

## Why the project is not blocked by this

The Risk Engine is multi-signal precisely so that one silent layer does not
silence the system. With forensics contributing nothing, VeriShield still
catches:

- **Aadhaar** — Verhoeff checksum. Catches every single-digit error and every
  adjacent transposition, verified exhaustively in tests. A fabricated number
  passes about one time in ten.
- **PAN** — the fifth character is the holder's surname initial, so the number
  and the printed name must agree. Editing one without the other is visible.
- **Certificates** — each total is printed twice, in digits and in words.
  Raising a mark means altering both, consistently.
- **Passports** — ICAO check digits, where a single edited digit breaks two of
  them.

All of that is arithmetic on what the document states about itself. It needs no
training data, no calibration, and no model — which is exactly why it works
today while Layer 5 does not.

## To revisit this

1. Obtain real tampered documents (DocTamper, CASIA v2, CoMoFoD, or
   photographed forgeries), into `backend/data/datasets/external/`.
2. Point `scripts/calibrate_forensics.py` at them.
3. If a detector reaches TPR ≥ 30% at FPR ≤ 10% **with localisation ≥ 50%**,
   enable it and record the operating point here.

The localisation requirement is not optional. A detector that identifies
tampered documents but cannot say where the tampering is produces a score a
reviewer has no way to check — and an unverifiable accusation is the one thing
this system is built not to make.
