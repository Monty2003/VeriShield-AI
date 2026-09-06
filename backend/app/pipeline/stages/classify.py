"""
Document classification (Layer 2).

Phase 1 is a transparent keyword-and-pattern classifier over OCR text. That
is not a stopgap for a missing model -- it is the right first implementation.
Identity documents announce themselves in fixed, printed language ("INCOME TAX
DEPARTMENT", "GOVERNMENT OF INDIA", an ICAO MRZ band), and matching that is
accurate, instant, needs no training data, and can explain itself: it can name
the exact phrase that drove the decision. A YOLO/LayoutLM classifier (Phase 2)
should be judged against this baseline, not assumed to beat it.

The UNKNOWN outcome is load-bearing. A classifier forced to choose among N
known types will confidently mislabel the first document it has never seen,
and a wrong document type routes the whole downstream rule engine to the wrong
rulebook -- producing confident, well-formatted nonsense.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.document import DocumentType
from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal


@dataclass
class TypeEvidence:
    """A matched cue and what it contributes."""

    cue: str
    weight: float
    matched: str


# Confidence below which we decline to name a type. Set high on purpose:
# a wrong type is more damaging than no type, because it silently swaps the
# entire rulebook rather than admitting uncertainty.
MIN_TYPE_CONFIDENCE = 0.45

# Each rule is (compiled pattern, weight, human-readable cue name). Weights are
# roughly "how uniquely does this phrase identify this document type".
_RULES: dict[DocumentType, list[tuple[re.Pattern[str], float, str]]] = {
    DocumentType.PASSPORT: [
        (re.compile(r"^P[<A-Z][A-Z]{3}[A-Z<]{10,}$", re.M), 0.75, "ICAO MRZ line 1"),
        (re.compile(r"\bPASSPORT\b", re.I), 0.35, "the word 'passport'"),
        (re.compile(r"\bREPUBLIC OF INDIA\b", re.I), 0.20, "'Republic of India'"),
        (re.compile(r"\bTYPE\s*[/:]?\s*P\b", re.I), 0.15, "document type 'P'"),
        (re.compile(r"\bPLACE OF ISSUE\b", re.I), 0.15, "'Place of Issue' field"),
    ],
    DocumentType.PAN: [
        (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), 0.55, "PAN number format"),
        (re.compile(r"\bINCOME TAX DEPARTMENT\b", re.I), 0.45, "'Income Tax Department'"),
        (re.compile(r"\bPERMANENT ACCOUNT NUMBER\b", re.I), 0.45, "'Permanent Account Number'"),
        (re.compile(r"\bFATHER'?S NAME\b", re.I), 0.10, "'Father's Name' field"),
    ],
    DocumentType.AADHAAR: [
        (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), 0.40, "12-digit Aadhaar format"),
        (re.compile(r"\bAADHAAR\b", re.I), 0.45, "the word 'Aadhaar'"),
        (re.compile(r"\bUNIQUE IDENTIFICATION AUTHORITY\b", re.I), 0.45, "UIDAI name"),
        (re.compile(r"\bआधार\b"), 0.40, "'Aadhaar' in Devanagari"),
        (re.compile(r"\bVID\s*:?\s*\d", re.I), 0.20, "Virtual ID field"),
    ],
    DocumentType.DRIVING_LICENCE: [
        (re.compile(r"\bDRIVING LICEN[CS]E\b", re.I), 0.60, "'Driving Licence'"),
        (re.compile(r"\b[A-Z]{2}[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{7}\b"), 0.35, "DL number format"),
        (re.compile(r"\b(LMV|MCWG|HMV|TRANS)\b"), 0.30, "vehicle class code"),
        (re.compile(r"\bDATE OF ISSUE\b", re.I), 0.10, "'Date of Issue' field"),
    ],
    DocumentType.VOTER_ID: [
        (re.compile(r"\bELECTION COMMISSION\b", re.I), 0.60, "'Election Commission'"),
        (re.compile(r"\bELECTOR'?S? PHOTO IDENTITY\b", re.I), 0.55, "EPIC title"),
        (re.compile(r"\b[A-Z]{3}\d{7}\b"), 0.30, "EPIC number format"),
    ],
    DocumentType.VISA: [
        (re.compile(r"^V[<A-Z][A-Z]{3}", re.M), 0.60, "MRV machine-readable line"),
        (re.compile(r"\bVISA\b", re.I), 0.40, "the word 'visa'"),
        (re.compile(r"\b(ENTRIES|DURATION OF STAY)\b", re.I), 0.30, "visa condition fields"),
    ],
    DocumentType.CERTIFICATE: [
        (re.compile(r"\b(UNIVERSITY|INSTITUTE|BOARD OF)\b", re.I), 0.35, "issuing institution"),
        (re.compile(r"\b(DEGREE|DIPLOMA|MARKSHEET|CERTIFICATE)\b", re.I), 0.40, "credential word"),
        (re.compile(r"\b(SEMESTER|CGPA|PERCENTAGE|ROLL NO)\b", re.I), 0.30, "academic fields"),
    ],
}


def classify_text(text: str) -> tuple[DocumentType, float, list[TypeEvidence]]:
    """
    Score every document type against the text and return the best.

    Returns UNKNOWN when nothing clears MIN_TYPE_CONFIDENCE, or when the top
    two types are too close to separate -- an ambiguous match is a reason to
    ask a human, not to pick the higher number and hope.
    """
    scores: dict[DocumentType, float] = {}
    evidence: dict[DocumentType, list[TypeEvidence]] = {}

    for doc_type, rules in _RULES.items():
        total = 0.0
        found: list[TypeEvidence] = []
        for pattern, weight, cue in rules:
            match = pattern.search(text)
            if match:
                total += weight
                found.append(
                    TypeEvidence(cue=cue, weight=weight, matched=match.group(0)[:48])
                )
        if total > 0:
            scores[doc_type] = min(1.0, total)
            evidence[doc_type] = found

    if not scores:
        return DocumentType.UNKNOWN, 0.0, []

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0]

    if best_score < MIN_TYPE_CONFIDENCE:
        return DocumentType.UNKNOWN, best_score, evidence.get(best_type, [])

    # Ambiguity guard: two types within 0.15 of each other is not a decision.
    if len(ranked) > 1 and (best_score - ranked[1][1]) < 0.15:
        return DocumentType.UNKNOWN, best_score, evidence.get(best_type, [])

    return best_type, best_score, evidence[best_type]


def classify(text: str) -> tuple[DocumentType, float, list[Signal]]:
    """Classify and emit the accompanying Signal."""
    doc_type, confidence, evidence = classify_text(text)

    if doc_type == DocumentType.UNKNOWN:
        return (
            doc_type,
            confidence,
            [
                signal(
                    code="classify.unknown",
                    stage=Stage.CLASSIFY,
                    title="Document type",
                    status=SignalStatus.FAIL,
                    severity=Severity.MEDIUM,
                    confidence=0.9,
                    # Blocking, not merely scored. An unidentified document had
                    # no rulebook applied to it, so a low risk score means only
                    # "we found nothing", never "we checked and it was fine".
                    # Auto-accepting on that basis would approve a document the
                    # system never actually examined.
                    blocking=True,
                    reason=(
                        "The document type could not be identified with enough "
                        "confidence to apply a specific rulebook. Only generic "
                        "checks were run, so this assessment cannot support "
                        "acceptance. Manual verification is required."
                        + (
                            f" Closest match scored {confidence:.0%}, below the "
                            f"{MIN_TYPE_CONFIDENCE:.0%} threshold."
                            if confidence
                            else ""
                        )
                    ),
                    evidence={
                        "best_score": round(confidence, 3),
                        "threshold": MIN_TYPE_CONFIDENCE,
                        "cues_found": [e.cue for e in evidence],
                    },
                )
            ],
        )

    cue_list = ", ".join(f"{e.cue} ({e.matched!r})" for e in evidence[:4])
    return (
        doc_type,
        confidence,
        [
            signal(
                code="classify.identified",
                stage=Stage.CLASSIFY,
                title="Document type",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                confidence=confidence,
                reason=(
                    f"Identified as {doc_type.value.replace('_', ' ')} "
                    f"({confidence:.0%} confidence) from: {cue_list}."
                ),
                evidence={
                    "type": doc_type.value,
                    "score": round(confidence, 3),
                    "cues": [
                        {"cue": e.cue, "weight": e.weight, "matched": e.matched}
                        for e in evidence
                    ],
                },
            )
        ],
    )
