# Datasets — where to put what

Nothing in this tree is committed to git except this file and the `.gitkeep`
markers. See the "Datasets" block in the repo's `.gitignore`.

> **Read this first.** Real identity documents must never enter git history.
> A leaked Aadhaar or passport scan cannot be un-published, and git keeps it
> even after you delete the file. The ignore rules here are deliberately
> broad. Do not add exceptions to them.

---

## `raw/` — you put clean documents here

**This is the folder you fill in.** Drop clean, untampered document images
into the subfolder matching their type:

```
raw/
  passport/          <- put passport images here
  pan/
  aadhaar/
  driving_licence/
  voter_id/
  certificate/
  other/
```

Filenames do not matter. `.jpg`, `.jpeg`, and `.png` are all read.

### What makes a good input image

The tamper generator and the forensic detectors both depend on properties
that only real captures have, so this matters more than the count:

- **Photograph or scan a real document.** Paper grain, print texture and
  sensor noise are the signals ELA and noise analysis actually measure. A
  screenshot or a synthetic mock-up has none of them, which is exactly why the
  detectors could not be calibrated against my synthetic test images.
- **JPEG straight from the camera is ideal.** ELA compares compression
  histories; a PNG has none, and a screenshot has the wrong one.
- **Fill the frame, keep it in focus,** roughly 1000px or more on the long edge.
- **Do not pre-edit them.** No cropping in Photoshop, no filters. The point is
  that these are the "clean" ground truth.

### How many

- **20–30 total** is enough to calibrate the classical detectors (ELA,
  copy-move) — that is threshold tuning, not training.
- **Thousands** would be needed to train the Swin tampering model. That is a
  later phase, and public datasets are the realistic route there.

### Privacy

Prefer **specimen/sample documents**, or **your own documents that you will
never share**. If you use your own, remember the generated outputs in
`generated/` contain the same personal data — that whole tree is git-ignored,
but it still lives on your disk.

---

## `generated/` — the tamper generator writes here

Produced by `scripts/generate_tampered_dataset.py`. Do not edit by hand.

```
generated/
  clean/        copies of the source images, re-encoded consistently
  tampered/     one tampered variant per operation
  masks/        pixel-level ground truth (white = tampered region)
  manifest.jsonl   one JSON record per generated image
```

The masks are the reason this is worth doing: with pixel-level ground truth,
detector calibration becomes a measurement (true/false positive rates at a
chosen threshold) instead of a guess.

---

## `external/` — public datasets go here

Downloaded research datasets, one folder each:

```
external/
  doctamper/     document-specific text manipulation. Closest fit to this project.
  casia2/        the standard splicing/copy-move benchmark. General images.
  comofod/       copy-move specific, with post-processing variants.
```

These generally sit behind a registration form or a Drive link that has to be
accepted manually, so downloading them is your step, not something the code
does. Unpack each into its own folder and keep the dataset's own directory
layout — the loaders will be written against it rather than the other way round.

### `external/specimens/` — public specimen documents

Populated by `scripts/fetch_specimen_documents.py`, which pulls official
specimen passport pages from Wikimedia Commons. Governments publish these as
public reference material: real ICAO layout and a real MRZ, describing no
actual person.

`manifest.json` records the licence, author and source URL for every file.

**Use these for MRZ, OCR and classifier validation only.** They are not valid
for forensic calibration: Wikimedia re-encodes every upload, which destroys
exactly the compression history and sensor noise that ELA and noise analysis
measure. A detector tuned on them would learn the properties of image hosting,
not of tampering.

The search that finds them is keyword-based and imprecise — the results mix
genuine bio-data pages with travel permits, landing slips and interior passport
pages. Check what you actually got before relying on any one file.

## What is deliberately NOT here

Real people's identity documents scraped from the web. Leaked Aadhaar, PAN and
passport scans are real individuals' sensitive data, and this project does not
collect them. Separately, they would not work: web-hosted images have been
resized and re-encoded by CDNs, which erases the forensic signal that
calibration depends on.

The images that DO work for calibration are the ones only you can produce —
your own camera captures, in `raw/`.
