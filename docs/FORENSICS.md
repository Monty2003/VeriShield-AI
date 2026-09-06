# Layer 5 — Forensics: current status

This document exists because the honest status of the forensics layer is not
what the architecture diagram implies, and anyone building on it needs to know
that before they demo it.

## What ships enabled

| Detector | Status | Why |
|---|---|---|
| Noise consistency | **Enabled** | Validated not to fire on text after texture masking. Passes clean documents. |
| EXIF / metadata | **Enabled** | Deterministic. Reads what the file states; makes no inference. |
| Error Level Analysis | **Disabled** (`enable_ela=True` to opt in) | Fails validation — see below. |
| Copy-move | **Disabled** (`enable_copy_move=True` to opt in) | Fails validation — see below. |

## Why two detectors are disabled

Both were measured against three versions of the same synthetic document:
clean, spliced (a patch re-encoded at a different quality), and copy-move
(a background patch cloned over the date of birth).

**Copy-move returned an identical region for all three inputs.** It locks onto
the regular vertical spacing of the document's own field rows and never sees
the edit. This is not a tuning problem. Identity documents are deliberately
repetitive — ruled field rows, guilloche security backgrounds, microtext, MRZ
filler runs — and evenly repeated structure is *geometrically indistinguishable*
from a cloned patch. Harmonic-offset suppression (rejecting offsets that also
appear at 2× and 3×) was implemented and did not resolve it.

**ELA reported FAIL on a blank synthetic document** — a flat grey rectangle with
one header bar and one line of text, containing nothing to tamper with — while
reporting PASS on the deliberately spliced one. Two real bugs were found and
fixed along the way (it was comparing text cells against background cells, so it
merely rediscovered where the writing was; and duplicate texture quantiles on
flat images silently destroyed the stratification). The implementation is now
principled. It is still uncalibrated, and a detector that is confident where
there is no document and silent where there is an edit cannot be shipped on.

## Why they are kept rather than deleted

Both approaches are sound in the literature. What is missing is not the
algorithm, it is the **ground truth needed to set thresholds**. Every constant
in these detectors is currently a guess, and guesses cannot be validated
against synthetic images that lack the paper grain, print texture and sensor
noise that real documents have — the very signals both methods depend on.

## What unblocks this

A labelled tampered-document dataset. In rough order of usefulness:

- **DocTamper** — document-specific, text-manipulation focused. Closest fit.
- **CASIA v2** — the standard splicing/copy-move benchmark. General images.
- **CoMoFoD** — copy-move specific, with post-processing variants.
- **Self-built** — photograph 20–30 real documents, edit half in an image
  editor, keep the originals. Small but *exactly* on-distribution, and the
  fastest route to a usable threshold.

With any of these, the calibration procedure is:

1. Run each detector across the labelled set, collecting raw scores.
2. Plot the score distributions for tampered vs. clean.
3. Choose thresholds at a chosen false-positive rate — for identity
   verification, prefer a low FPR: a false accusation costs a real person far
   more than a missed forgery costs the system, which then falls back to a
   human reviewer anyway.
4. Record the measured TPR/FPR in this file and enable the detector.

## The wider point

This is the reason the Risk Engine is multi-signal and the reason MRZ
validation was built first. Check-digit arithmetic is deterministic and
verifiable today; forensics is probabilistic and needs data. A system that
depends on a single tampering model would currently have nothing to say. This
one still catches edited passport fields with certainty, because that evidence
comes from arithmetic rather than inference.
