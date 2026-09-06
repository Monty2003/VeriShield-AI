# VeriShield AI

Multimodal identity and credential **risk assessment**.

VeriShield does not declare a document genuine or fake. It gathers evidence
from independent checks, scores the risk, and hands a human reviewer the
reasoning behind that score. Every risk point traces back to a named signal
with a stated reason.

---

## Status

Working end-to-end today, without the ML stack installed:

- **Passport MRZ validation** — ICAO 9303 check-digit arithmetic, verified
  against the published specimen. Deterministic, no model required.
- **Document classification** — keyword/pattern based, with a real `UNKNOWN`
  outcome rather than a forced guess.
- **Field extraction** — MRZ, PAN, Aadhaar (masked in all output).
- **Risk engine** — transparent additive weighting, full per-signal derivation.
- **Cross-document consistency** — fuzzy name matching and date-of-birth
  comparison, tolerant of transliteration and clerical convention.
- **OCR** — PaddleOCR, validated against 14 real specimen passports.
- **REST API** — FastAPI with generated docs at `/docs`.
- **88 tests passing.**

Not yet implemented: face verification (Layer 6), the authority-record
registry (Layer 8), and the React dashboard. Forensics (Layer 5) is partially
disabled — **read [docs/FORENSICS.md](docs/FORENSICS.md), it explains why.**

---

## Quick start

```bash
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # ~1 min

./.venv/Scripts/python.exe -m pytest                            # 66 tests
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Open http://localhost:8000/docs.

`GET /health` reports which capabilities this deployment actually has, and
names in plain language what is degraded and why.

### Optional: data layer

```bash
docker compose up -d      # MongoDB, Redis, Qdrant, MinIO
```

Not required. The pipeline is pure computation over an image; these provide
persistence, object storage and the mock authority registry.

### Optional: ML models

```bash
cd backend
./.venv/Scripts/python.exe -m pip install -r requirements-ml.txt
```

Large download. Adds PaddleOCR (so real images can be read rather than text
being supplied), plus the face and layout models.

> **6 GB VRAM note.** The target GPU is an RTX 3050. It fits any *one* of these
> models comfortably and cannot hold them all at once, so models load and
> release on demand. TensorRT and Triton are deliberately left to last — on a
> 6 GB laptop they compete with the dev environment for VRAM and buy nothing
> for a demo.

---

## Try it without OCR installed

The pipeline accepts supplied text instead of running OCR. This exercises
every rule and risk path today, and doubles as the mechanism for a reviewer to
correct a misread field and re-run the assessment.

```bash
# A valid MRZ
curl -X POST http://localhost:8000/api/v1/verify/mrz \
  -F "line1=P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<" \
  -F "line2=L898902C36UTO7408122F1204159ZE184226B<<<<<10"
```

Change one digit of the date of birth and re-run: **two** check digits fail,
not one, because the composite digit covers every field. That redundancy is
why editing an MRZ by hand is detectable arithmetically.

---

## Architecture

```
     Document(s) + optional selfie
                  │
         ┌────────┴────────┐
         │  L1  Ingest     │  decode, resolution sanity
         ├─────────────────┤
         │  L2  Classify   │  type, or an honest UNKNOWN
         ├─────────────────┤
         │  L3  OCR        │  text + per-line confidence
         │      Extract    │  typed fields, provenance-tagged
         ├─────────────────┤
         │  L4  Validate   │  per-type rulebook, checksums
         ├─────────────────┤
         │  L5  Forensics  │  noise, metadata (ELA/copy-move gated)
         ├─────────────────┤
         │  L6  Face       │  not yet implemented
         ├─────────────────┤
         │  L7  Cross-doc  │  same person across documents?
         ├─────────────────┤
         │  L8  Registry   │  not yet implemented
         └────────┬────────┘
                  │   every stage emits Signals
                  ▼
          ┌───────────────┐
          │  Risk Engine  │  transparent additive weighting
          └───────┬───────┘
                  ▼
     score + band + decision + full derivation
```

### The Signal contract

Every stage emits `Signal` objects, never bare booleans. A Signal carries its
stage, status, severity, confidence, a plain-language reason, structured
evidence, and any image regions it refers to. The Risk Engine consumes *only*
Signals, so any score can always be traced to the evidence that produced it.

Five statuses, and the distinctions matter:

| Status | Meaning |
|---|---|
| `PASS` | Checked, satisfied. |
| `WARN` | Checked, suspicious but not disqualifying. |
| `FAIL` | Checked, failed. |
| `SKIP` | Did not apply. **Not** evidence of anything. |
| `ERROR` | Could not run. Missing evidence we expected. |

`SKIP` and `ERROR` are kept separate on purpose: "we did not check this" must
never look like "we checked this and it was fine".

### Fraud risk vs. validity

These are different questions, and conflating them produces wrong decisions.

An **expired passport is almost certainly genuine** — its fraud score *should*
be low — but it must never be auto-accepted. So signals can be marked
`blocking`: they cap the decision without inflating the fraud score. An expired
passport scores 12/100 (LOW risk, honestly) and still routes to manual review.

```
Clean, in date     0.0/100   LOW      → ACCEPT
Expired, genuine  12.0/100   LOW      → MANUAL_REVIEW   (blocked)
Edited MRZ        61.8/100   HIGH     → REJECT
```

### What real documents changed

The parser scored 25/25 on hand-built MRZ strings while finding a usable MRZ in
only **2 of 14** real passport photographs. Synthetic input had hidden the
whole problem.

General-purpose OCR is not trained on the MRZ filler character `<` and drops
runs of it, so real line 2 readings arrive at 33-35 characters instead of 44.
Handling that lifted MRZ recovery to **7 of 14** — which is every document in
the set that actually has an MRZ; the rest are travel permits, landing slips
and interior pages.

Three findings from that work are encoded in the code and its tests:

- **Repairing a line does not make its checks trustworthy.** Re-inflating the
  filler run restores positions 0-27 exactly, so the document number, date of
  birth and expiry check digits stay genuine. But the inserted filler has
  character value 0, the same as the personal check digit it displaces — so
  the personal-number and composite sums verify *no matter what the document
  said*. Those two are reported as `SKIP`, never `PASS`. Scoring them would
  launder our own repair into evidence.
- **Nationality is unprotected.** The TD3 composite spans positions 0-9, 13-19
  and 21-42 — it skips nationality at 10-12 entirely. PaddleOCR read Norway's
  `NOR` as `N0R` on a real document and every check digit still passed, so
  nationality is carried at reduced confidence.
- **A wrong name is worse than no name.** Line 1 is filler-delimited
  throughout, and when OCR drops those separators the surname/given boundary
  is unrecoverable. Reporting the collapsed string as a surname would flow
  into cross-document comparison and manufacture a mismatch against the same
  person's other documents, so the split is declined and only the full name
  is returned.

### Evidence coverage

Every assessment reports how much of the expected evidence it actually got.
A clean score computed from one check is not the same claim as a clean score
from twenty, so below 55% coverage the engine will not return `ACCEPT`.

The asymmetry is deliberate: **low risk on thin evidence escalates to a human;
high risk on thin evidence still rejects.** Spending reviewer time is cheaper
than wrongly clearing a document.

---

## Roadmap

**Phase 1 (current)** — transparent rule-based risk engine.
**Phase 2** — learned weights, judged against the Phase 1 baseline rather than
assumed to beat it. `RiskModel` is a Protocol, and its return type forces
per-signal contributions, so no future model can quietly become a black box.
**Phase 3** — hybrid.

Immediate next steps, in dependency order:

1. **A labelled tampered-document dataset.** Unblocks all of Layer 5. See
   [docs/FORENSICS.md](docs/FORENSICS.md). Put clean document photographs in
   `backend/data/datasets/raw/<type>/`, then run:

   ```bash
   python scripts/generate_tampered_dataset.py --variants 3
   ```

   This produces tampered variants with pixel-level ground-truth masks, so
   detector thresholds can be *measured* rather than guessed. See
   [backend/data/datasets/README.md](backend/data/datasets/README.md).
2. Install `requirements-ml.txt`, wire PaddleOCR, validate on real document photos.
3. Face verification (Layer 6) — RetinaFace + ArcFace, plus liveness.
4. Mock authority registry (Layer 8) with a swappable interface for real APIs.
5. React dashboard.

---

## Project layout

```
backend/
  app/
    schemas/       Signal contract, document + result models
    rules/         mrz.py (ICAO 9303), passport.py, registry.py
    pipeline/
      stages/      ingest, classify, ocr, extract, forensics, cross_document
      orchestrator.py
    risk/engine.py Transparent weighted model + RiskModel protocol
    api/routes/    verify.py, health.py
  tests/           66 tests
  data/datasets/   raw/ (you fill), generated/, external/ -- all git-ignored
scripts/
  generate_tampered_dataset.py   labelled tamper data from clean documents
docs/FORENSICS.md  Honest status of Layer 5
docker-compose.yml Mongo, Redis, Qdrant, MinIO (all optional)
```

---

## A note on scope

Real UIDAI, passport and DL verification APIs are not openly accessible. The
authority-record layer is therefore designed as a swappable interface with a
synthetic registry behind it. The honest claim is:

> *"The system detects identity duplication and inconsistency when authorized
> records are available."*

Not that it can identify anyone from a national database.
