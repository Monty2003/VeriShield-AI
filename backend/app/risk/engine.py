"""
The Risk Engine.

Consumes Signals and produces a score, a band, and a decision -- along with
the full derivation of how it got there.

Design constraint, deliberately chosen: Phase 1 is a TRANSPARENT weighted
model, not a learned one. Every risk point on the final score can be traced
to a named signal with a stated weight, and a reviewer can be shown the
arithmetic. A neural net that outputs 64/100 with no derivation is worse than
useless to someone who must justify a decision to a person whose visa or job
offer depends on it.

`RiskModel` is a protocol so Phase 2 (learned weights) and Phase 3 (hybrid)
can replace the scoring function without touching the pipeline. The learned
model must still emit per-signal contributions -- explainability is a hard
requirement of the interface, not a feature of the current implementation.
"""

from __future__ import annotations

from typing import Protocol

from app.schemas.document import Decision, RiskAssessment, RiskBand
from app.schemas.signals import Severity, Signal, SignalStatus, Stage

# Band thresholds on a 0-100 risk scale.
BAND_MEDIUM_THRESHOLD = 30.0
BAND_HIGH_THRESHOLD = 65.0

# Below this evidence coverage, we will not hand back a clean ACCEPT no matter
# how low the score. A low score computed from three checks is not the same
# claim as a low score computed from twenty, and collapsing that distinction
# is how verification systems get fooled by unreadable inputs.
MIN_CONFIDENCE_FOR_ACCEPT = 0.55

# Stages we expect to contribute evidence for a typical document with a photo.
# Used to measure coverage; stages that legitimately do not apply are excluded
# by the caller rather than counted as missing.
DEFAULT_EXPECTED_STAGES = (
    Stage.CLASSIFY,
    Stage.OCR,
    Stage.EXTRACT,
    Stage.VALIDATE,
    Stage.FORENSICS,
)


class RiskModel(Protocol):
    """
    Contract every risk model must satisfy, learned or not.

    The return type forces per-signal contributions and human-readable
    reasons, so no future model can quietly become a black box.
    """

    def assess(self, signals: list[Signal], expected_stages: tuple[Stage, ...]) -> RiskAssessment:
        ...


class WeightedRiskModel:
    """
    Phase 1: additive weighted risk.

    score = min(100, sum over signals of (severity_weight * confidence * status_multiplier))

    Additive rather than averaged, because risk evidence accumulates: a
    document with a broken checksum AND a face mismatch AND a date
    inconsistency is worse than either alone, and an average would dilute
    exactly the stacking that should alarm us.
    """

    def __init__(
        self,
        *,
        medium_threshold: float = BAND_MEDIUM_THRESHOLD,
        high_threshold: float = BAND_HIGH_THRESHOLD,
    ) -> None:
        self.medium_threshold = medium_threshold
        self.high_threshold = high_threshold

    # ---- coverage -------------------------------------------------------

    def _coverage(
        self, signals: list[Signal], expected_stages: tuple[Stage, ...]
    ) -> tuple[dict[str, str], float]:
        """
        Report which stages produced usable evidence, and derive confidence.

        A stage counts as having run only if it emitted at least one signal
        that is not SKIP/ERROR. Confidence is the fraction of expected stages
        that did so.
        """
        by_stage: dict[Stage, list[Signal]] = {}
        for s in signals:
            by_stage.setdefault(s.stage, []).append(s)

        coverage: dict[str, str] = {}
        ran = 0
        for stage in expected_stages:
            stage_signals = by_stage.get(stage, [])
            if not stage_signals:
                coverage[stage.value] = "skipped"
                continue
            if any(s.status == SignalStatus.ERROR for s in stage_signals) and not any(
                s.status in (SignalStatus.PASS, SignalStatus.WARN, SignalStatus.FAIL)
                for s in stage_signals
            ):
                coverage[stage.value] = "errored"
                continue
            coverage[stage.value] = "ran"
            ran += 1

        confidence = ran / len(expected_stages) if expected_stages else 0.0
        return coverage, confidence

    # ---- scoring --------------------------------------------------------

    def assess(
        self,
        signals: list[Signal],
        expected_stages: tuple[Stage, ...] = DEFAULT_EXPECTED_STAGES,
    ) -> RiskAssessment:
        coverage, confidence = self._coverage(signals, expected_stages)

        contributions: list[dict[str, object]] = []
        raw_score = 0.0

        for s in signals:
            points = s.contribution
            if points <= 0:
                continue
            raw_score += points
            contributions.append(
                {
                    "code": s.code,
                    "title": s.title,
                    "stage": s.stage.value,
                    "status": s.status.value,
                    "severity": s.severity.value,
                    "confidence": round(s.confidence, 3),
                    "points": round(points, 2),
                    "reason": s.reason,
                }
            )

        contributions.sort(key=lambda c: c["points"], reverse=True)
        score = min(100.0, raw_score)

        blocking = [s for s in signals if s.is_blocking_failure]
        blocking_reasons = [s.reason for s in blocking]

        band = self._band(score, signals)
        decision = self._decide(band, confidence, blocked=bool(blocking))
        reasons = self._reasons(contributions, band, confidence, coverage)

        return RiskAssessment(
            score=round(score, 1),
            band=band,
            decision=decision,
            confidence=round(confidence, 3),
            top_reasons=reasons,
            blocking_reasons=blocking_reasons,
            contributions=contributions,
            coverage=coverage,
        )

    def _band(self, score: float, signals: list[Signal]) -> RiskBand:
        """
        Map score to band, with one override.

        Any CRITICAL signal that outright FAILED forces HIGH regardless of the
        arithmetic. A failed composite MRZ checksum or a confirmed photo
        substitution is disqualifying on its own; it should not be averaged
        away by a document that is otherwise tidy.
        """
        critical_failure = any(
            s.severity == Severity.CRITICAL
            and s.status == SignalStatus.FAIL
            and s.confidence >= 0.7
            for s in signals
        )
        if critical_failure:
            return RiskBand.HIGH
        if score >= self.high_threshold:
            return RiskBand.HIGH
        if score >= self.medium_threshold:
            return RiskBand.MEDIUM
        return RiskBand.LOW

    def _decide(
        self, band: RiskBand, confidence: float, blocked: bool = False
    ) -> Decision:
        """
        Recommend an action.

        `blocked` is independent of the fraud score. An expired passport scores
        near zero for fraud -- correctly, it is a real passport -- but cannot be
        accepted. Without this gate a genuine expired document would be waved
        through, which was exactly the bug this parameter was added to fix.

        Note the asymmetry in the rest: low risk on thin evidence routes to a
        human, but high risk on thin evidence still routes to REJECT. We are
        willing to spend a reviewer's time to avoid wrongly clearing a
        document; we are not willing to auto-clear one we could barely read.
        """
        if band == RiskBand.HIGH:
            return Decision.REJECT
        if blocked:
            # Not REJECT: the holder may simply need to present a current
            # document, and that is a reviewer's call, not the engine's.
            return Decision.MANUAL_REVIEW
        if band == RiskBand.MEDIUM:
            return Decision.MANUAL_REVIEW
        if confidence < MIN_CONFIDENCE_FOR_ACCEPT:
            return Decision.MANUAL_REVIEW
        return Decision.ACCEPT

    def _reasons(
        self,
        contributions: list[dict[str, object]],
        band: RiskBand,
        confidence: float,
        coverage: dict[str, str],
    ) -> list[str]:
        """Build the ranked plain-language summary shown at the top of the report."""
        reasons = [str(c["reason"]) for c in contributions[:5]]

        if confidence < MIN_CONFIDENCE_FOR_ACCEPT:
            missing = [k for k, v in coverage.items() if v != "ran"]
            reasons.append(
                "Evidence is incomplete -- these checks did not produce a result: "
                + ", ".join(missing)
                + ". Score is based on partial information."
            )

        if not reasons and band == RiskBand.LOW:
            reasons.append("All checks that ran passed. No risk indicators found.")

        return reasons


def assess_document(
    signals: list[Signal],
    expected_stages: tuple[Stage, ...] = DEFAULT_EXPECTED_STAGES,
    model: RiskModel | None = None,
) -> RiskAssessment:
    """Score a single document. Default model is the Phase 1 weighted one."""
    return (model or WeightedRiskModel()).assess(signals, expected_stages)


def assess_case(
    document_risks: list[RiskAssessment],
    cross_document_signals: list[Signal],
    model: RiskModel | None = None,
) -> RiskAssessment:
    """
    Score a whole case: several documents plus the cross-document evidence.

    Case risk is NOT the mean of document risks. Two individually clean
    documents that disagree about the holder's date of birth are a serious
    finding, and averaging two low scores would hide it. So we take the worst
    document as the floor and add cross-document risk on top.
    """
    engine = model or WeightedRiskModel()
    cross = engine.assess(cross_document_signals, expected_stages=(Stage.CROSS_DOC,))

    worst_doc = max((r.score for r in document_risks), default=0.0)
    combined = min(100.0, worst_doc + cross.score)

    # Case confidence is the weakest link: a case is only as trustworthy as
    # its least-well-evidenced document.
    doc_confidence = min((r.confidence for r in document_risks), default=0.0)

    critical_failure = any(
        s.severity == Severity.CRITICAL and s.status == SignalStatus.FAIL
        for s in cross_document_signals
    )

    if critical_failure or combined >= BAND_HIGH_THRESHOLD:
        band = RiskBand.HIGH
    elif combined >= BAND_MEDIUM_THRESHOLD:
        band = RiskBand.MEDIUM
    else:
        band = RiskBand.LOW

    # A case can never be safer than its worst document.
    #
    # Band thresholds alone do not guarantee this: a document can be pushed to
    # HIGH by the critical-signal override while still scoring below the HIGH
    # threshold numerically. Deriving the case band from the score alone then
    # SOFTENS a document that was independently rejected -- which is how a
    # forged passport ends up merely "needs review" because it arrived in
    # company. The invariant is enforced explicitly rather than left to the
    # arithmetic.
    _BAND_ORDER = {RiskBand.LOW: 0, RiskBand.MEDIUM: 1, RiskBand.HIGH: 2}
    worst_doc_band = max(
        (r.band for r in document_risks),
        key=lambda b: _BAND_ORDER[b],
        default=RiskBand.LOW,
    )
    if _BAND_ORDER[worst_doc_band] > _BAND_ORDER[band]:
        band = worst_doc_band

    case_blocked = any(s.is_blocking_failure for s in cross_document_signals) or any(
        r.blocking_reasons for r in document_risks
    )
    decision = engine._decide(band, doc_confidence, blocked=case_blocked)

    reasons: list[str] = []
    if cross.contributions:
        reasons.extend(cross.top_reasons)
    worst = max(document_risks, key=lambda r: r.score, default=None)
    if worst and worst.top_reasons:
        reasons.extend(worst.top_reasons[:3])

    if not reasons:
        reasons.append(
            "All documents passed their individual checks and are mutually consistent."
        )

    return RiskAssessment(
        score=round(combined, 1),
        band=band,
        decision=decision,
        confidence=round(doc_confidence, 3),
        top_reasons=reasons[:6],
        contributions=cross.contributions,
        coverage={"cross_document": "ran" if cross_document_signals else "skipped"},
    )
