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
#
# Invariant: NO bare identifier-format pattern may reach MIN_TYPE_CONFIDENCE on
# its own. Identifier shapes -- five letters then four digits then a letter, a
# twelve-digit run -- are exactly what OCR noise produces by accident on dense
# text, so a document must also show the issuer's printed wording before it is
# assigned a type. Printed institutional text ("INCOME TAX DEPARTMENT") is
# evidence OCR does not invent; a character pattern is not.
#
# This is enforced by test_no_bare_identifier_pattern_can_classify_alone.
_RULES: dict[DocumentType, list[tuple[re.Pattern[str], float, str]]] = {
    DocumentType.PASSPORT: [
        (re.compile(r"^P[<A-Z][A-Z]{3}[A-Z<]{10,}$", re.M), 0.75, "ICAO MRZ line 1"),
        (re.compile(r"\bPASSPORT\b", re.I), 0.35, "the word 'passport'"),
        (re.compile(r"\bREPUBLIC OF INDIA\b", re.I), 0.20, "'Republic of India'"),
        (re.compile(r"\bTYPE\s*[/:]?\s*P\b", re.I), 0.15, "document type 'P'"),
        (re.compile(r"\bPLACE OF ISSUE\b", re.I), 0.15, "'Place of Issue' field"),
    ],
    DocumentType.PAN: [
        # 0.30, below MIN_TYPE_CONFIDENCE: this shape alone must not decide a
        # type. At 0.55 it did -- one OCR-fabricated string was enough to
        # classify any document as a PAN card with no corroboration at all,
        # which then applies the wrong rulebook to it.
        (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), 0.30, "PAN number format"),
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


# --- fuzzy issuer wording --------------------------------------------------
#
# Exact regexes miss real documents. Measured across 217 Aadhaar cards, only
# 46 matched any issuer pattern; the rest were read as "Govemment of India",
# "GOVERNIENT OFINCAA", "/Your Aadhar No." -- genuine cards whose printed text
# OCR mangled. Requiring exact spelling threw away three quarters of the set.
#
# Only DISCRIMINATIVE wording belongs here. "Government of India" is printed on
# Aadhaar cards AND PAN cards AND passports, so fuzzy-matching it would push
# every Indian document toward whichever type claimed it -- a cue that appears
# on everything identifies nothing.
FUZZY_THRESHOLD = 82.0

_FUZZY_CUES: dict[DocumentType, list[tuple[str, float, str]]] = {
    DocumentType.AADHAAR: [
        ("UNIQUE IDENTIFICATION AUTHORITY OF INDIA", 0.45, "UIDAI name"),
        ("AADHAAR", 0.40, "the word 'Aadhaar'"),
        ("YOUR AADHAAR NO", 0.45, "'Your Aadhaar No.' label"),
        ("MERA AADHAAR MERI PEHCHAAN", 0.40, "UIDAI tagline"),
        # 0.15, deliberately too small to decide anything. This wording is
        # printed on PAN cards and passports too, so it can only ever
        # corroborate a type that other evidence already points at. It earns
        # its place because on badly degraded scans it is often the only
        # institutional text OCR still recovers, and combined with a 12-digit
        # number it tips a genuine Aadhaar card over the threshold.
        ("GOVERNMENT OF INDIA", 0.15, "'Government of India' (shared wording)"),
    ],
    DocumentType.PAN: [
        ("INCOME TAX DEPARTMENT", 0.45, "'Income Tax Department'"),
        ("PERMANENT ACCOUNT NUMBER", 0.45, "'Permanent Account Number'"),
    ],
    DocumentType.DRIVING_LICENCE: [
        ("DRIVING LICENCE", 0.55, "'Driving Licence'"),
        ("TRANSPORT DEPARTMENT", 0.35, "'Transport Department'"),
    ],
    DocumentType.VOTER_ID: [
        ("ELECTION COMMISSION OF INDIA", 0.55, "'Election Commission of India'"),
        ("ELECTORS PHOTO IDENTITY CARD", 0.50, "EPIC title"),
    ],
    DocumentType.PASSPORT: [
        ("REPUBLIC OF INDIA", 0.20, "'Republic of India'"),
        ("PASSPORT", 0.35, "the word 'passport'"),
    ],
}


# --- document sides --------------------------------------------------------
#
# People photograph both faces of a card, and only one of them carries identity
# data. Measured on real captures: 4 of 8 PAN photographs and 2 of 4 marksheet
# photographs were reverse sides. Without this, every one of them came back
# UNKNOWN at 0% confidence -- a perfectly good photograph of a real document
# reported as unrecognisable, which tells the user nothing about what to do.
#
# The reverse of each document type has its own fixed printed wording, so it
# identifies itself just as reliably as the front does -- it simply says
# something different.
_BACK_CUES: dict[DocumentType, list[tuple[re.Pattern[str], str]]] = {
    DocumentType.PAN: [
        (re.compile(r"INCOME\s*TAX\s*PAN\s*SERVICES\s*UNIT", re.I), "PAN Services Unit address"),
        (re.compile(r"IF\s*THIS\s*CARD\s*IS\s*LOST", re.I), "lost-card return notice"),
        (re.compile(r"PROTEAN\s*EGOV|NSDL\s*E-?GOV", re.I), "PAN issuing agency"),
    ],
    DocumentType.AADHAAR: [
        (re.compile(r"ADDRESS\s*:", re.I), "address block"),
        (re.compile(r"\bVID\s*:?\s*\d", re.I), "Virtual ID"),
        (re.compile(r"HELP@UIDAI|WWW\.UIDAI", re.I), "UIDAI contact details"),
        (re.compile(r"1947", re.I), "UIDAI helpline number"),
    ],
    DocumentType.CERTIFICATE: [
        (re.compile(r"EXAMINATION\s*BYE-?LAWS", re.I), "examination byelaws"),
        (re.compile(r"POSITIONAL\s*GRADE", re.I), "grading explanation"),
        (re.compile(r"GRADING\s*PATTERN|9-?POINT", re.I), "grading scale notes"),
    ],
}

# A back side must show at least this many of its cues. One alone is too easy
# to hit by accident -- "address:" appears on plenty of document fronts.
MIN_BACK_CUES = 2


def detect_side(text: str, doc_type: DocumentType) -> tuple[str, list[str]]:
    """
    Decide whether an image shows the front or the reverse of a document.

    Returns ("front" | "back" | "unknown", matched cue names).
    """
    cues = _BACK_CUES.get(doc_type, [])
    matched = [name for pattern, name in cues if pattern.search(text)]

    if len(matched) >= MIN_BACK_CUES:
        return "back", matched

    # Identity fields living on the front are themselves front evidence.
    front_markers = [
        p for p, _, _ in _RULES.get(doc_type, []) if p.search(text)
    ]
    if front_markers:
        return "front", []

    return "unknown", matched


def _fuzzy_find(text_upper: str, phrase: str) -> tuple[bool, float]:
    """
    Look for a phrase allowing for OCR corruption.

    partial_ratio, because the phrase is a fragment of a much longer OCR dump
    and a whole-string comparison would score near zero regardless of whether
    the phrase is present.
    """
    from rapidfuzz import fuzz

    # Short phrases need a tighter threshold: at seven characters, "AADHAAR"
    # is within a couple of edits of plenty of unrelated words.
    threshold = FUZZY_THRESHOLD + (8.0 if len(phrase) <= 10 else 0.0)
    score = fuzz.partial_ratio(phrase, text_upper)
    return score >= threshold, float(score)


def classify_text(text: str) -> tuple[DocumentType, float, list[TypeEvidence]]:
    """
    Score every document type against the text and return the best.

    Returns UNKNOWN when nothing clears MIN_TYPE_CONFIDENCE, or when the top
    two types are too close to separate -- an ambiguous match is a reason to
    ask a human, not to pick the higher number and hope.
    """
    scores: dict[DocumentType, float] = {}
    evidence: dict[DocumentType, list[TypeEvidence]] = {}
    text_upper = text.upper()

    for doc_type, rules in _RULES.items():
        total = 0.0
        found: list[TypeEvidence] = []
        matched_cues: set[str] = set()

        for pattern, weight, cue in rules:
            match = pattern.search(text)
            if match:
                total += weight
                matched_cues.add(cue)
                found.append(
                    TypeEvidence(cue=cue, weight=weight, matched=match.group(0)[:48])
                )

        # Fuzzy pass, for issuer wording OCR corrupted. A cue already matched
        # exactly is not counted twice -- otherwise a clean document would
        # score double for the same evidence and outrank a degraded one purely
        # for being easy to read.
        for phrase, weight, cue in _FUZZY_CUES.get(doc_type, []):
            if cue in matched_cues:
                continue
            hit, score = _fuzzy_find(text_upper, phrase)
            if hit:
                total += weight
                found.append(
                    TypeEvidence(
                        cue=f"{cue} (approximate, {score:.0f}% match)",
                        weight=weight,
                        matched=phrase[:48],
                    )
                )

        if total > 0:
            scores[doc_type] = min(1.0, total)
            evidence[doc_type] = found

    if not scores:
        # No front-side evidence. Before giving up, check whether this is the
        # REVERSE of a known document -- a real photograph of a real card,
        # just the face that carries no identity fields.
        for doc_type, cues in _BACK_CUES.items():
            matched = [(name, p) for p, name in cues if p.search(text)]
            if len(matched) >= MIN_BACK_CUES:
                return (
                    doc_type,
                    min(1.0, 0.35 + 0.15 * len(matched)),
                    [
                        TypeEvidence(cue=f"{name} (reverse side)", weight=0.15, matched=pat.pattern[:40])
                        for name, pat in matched
                    ],
                )
        return DocumentType.UNKNOWN, 0.0, []

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0]

    if best_score < MIN_TYPE_CONFIDENCE:
        return DocumentType.UNKNOWN, best_score, evidence.get(best_type, [])

    # Ambiguity guard: two types within 0.15 of each other is not a decision.
    if len(ranked) > 1 and (best_score - ranked[1][1]) < 0.15:
        return DocumentType.UNKNOWN, best_score, evidence.get(best_type, [])

    return best_type, best_score, evidence[best_type]


def classify(text: str) -> tuple[DocumentType, float, str, list[Signal]]:
    """
    Classify and emit the accompanying Signals.

    Returns (type, confidence, side, signals). The side matters downstream:
    a reverse-side image legitimately has no identity fields, so the extract
    stage must not report their absence as a failure -- and the risk engine
    must not accept an identity that was never shown.
    """
    doc_type, confidence, evidence = classify_text(text)

    if doc_type == DocumentType.UNKNOWN:
        return (
            doc_type,
            confidence,
            "unknown",
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

    side, back_cues = detect_side(text, doc_type)
    cue_list = ", ".join(f"{e.cue} ({e.matched!r})" for e in evidence[:4])

    signals: list[Signal] = []
    if side == "back":
        signals.append(
            signal(
                code="classify.reverse_side",
                stage=Stage.CLASSIFY,
                title="Document side",
                status=SignalStatus.WARN,
                severity=Severity.LOW,
                confidence=0.85,
                # Blocking, but low severity. This is a good photograph of a
                # real document -- it simply shows the face without the
                # identity data, so nothing about the holder was verified.
                # The right response is "send the other side", not "rejected".
                blocking=True,
                reason=(
                    f"This is the reverse side of a "
                    f"{doc_type.value.replace('_', ' ')} "
                    f"(recognised from: {', '.join(back_cues)}). The identity "
                    f"fields are printed on the front, so nothing about the "
                    f"holder could be read or verified from this image. Submit "
                    f"the front of the document as well."
                ),
                evidence={"side": "back", "cues": back_cues},
            )
        )

    return (
        doc_type,
        confidence,
        side,
        signals
        + [
            signal(
                code="classify.identified",
                stage=Stage.CLASSIFY,
                title="Document type",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                confidence=confidence,
                reason=(
                    f"Identified as {doc_type.value.replace('_', ' ')}"
                    f"{' (reverse side)' if side == 'back' else ''} "
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
