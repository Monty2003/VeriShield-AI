"""
Risk engine tests.

The behaviour these lock down is not "the score is 61.8" -- weights will be
retuned. It is the DECISION LOGIC: what must never be auto-accepted, what must
always escalate, and the separation between fraud risk and validity.
"""

from __future__ import annotations

from app.risk.engine import WeightedRiskModel, assess_document
from app.schemas.document import Decision, RiskBand
from app.schemas.signals import Severity, SignalStatus, Stage, signal

STAGES = (Stage.VALIDATE,)


def sig(status, severity, *, code="test.signal", blocking=False, confidence=1.0):
    return signal(
        code=code,
        stage=Stage.VALIDATE,
        title="Test signal",
        status=status,
        severity=severity,
        confidence=confidence,
        reason="test",
        blocking=blocking,
    )


class TestScoring:
    def test_all_pass_scores_zero(self):
        signals = [sig(SignalStatus.PASS, Severity.INFO) for _ in range(5)]
        assert assess_document(signals, STAGES).score == 0.0

    def test_risk_accumulates_across_signals(self):
        one = assess_document([sig(SignalStatus.FAIL, Severity.MEDIUM)], STAGES)
        three = assess_document(
            [sig(SignalStatus.FAIL, Severity.MEDIUM, code=f"t{i}") for i in range(3)],
            STAGES,
        )
        assert three.score > one.score

    def test_warn_contributes_less_than_fail(self):
        warn = assess_document([sig(SignalStatus.WARN, Severity.HIGH)], STAGES)
        fail = assess_document([sig(SignalStatus.FAIL, Severity.HIGH)], STAGES)
        assert warn.score < fail.score

    def test_low_confidence_reduces_contribution(self):
        certain = assess_document(
            [sig(SignalStatus.FAIL, Severity.HIGH, confidence=1.0)], STAGES
        )
        unsure = assess_document(
            [sig(SignalStatus.FAIL, Severity.HIGH, confidence=0.3)], STAGES
        )
        assert unsure.score < certain.score

    def test_score_is_capped_at_100(self):
        signals = [
            sig(SignalStatus.FAIL, Severity.CRITICAL, code=f"t{i}") for i in range(10)
        ]
        assert assess_document(signals, STAGES).score == 100.0

    def test_skip_contributes_no_risk(self):
        assert assess_document([sig(SignalStatus.SKIP, Severity.CRITICAL)], STAGES).score == 0.0


class TestBanding:
    def test_critical_failure_forces_high_band(self):
        """
        One disqualifying finding must not be averaged away by an otherwise
        tidy document. This is why _band has an override at all.
        """
        signals = [sig(SignalStatus.PASS, Severity.INFO, code=f"p{i}") for i in range(9)]
        signals.append(sig(SignalStatus.FAIL, Severity.CRITICAL, code="critical"))
        result = assess_document(signals, STAGES)
        assert result.band == RiskBand.HIGH
        assert result.decision == Decision.REJECT

    def test_low_severity_warnings_stay_low_band(self):
        signals = [sig(SignalStatus.WARN, Severity.LOW, code=f"w{i}") for i in range(3)]
        assert assess_document(signals, STAGES).band == RiskBand.LOW


class TestBlockingConditions:
    """
    Fraud risk and validity are different questions.

    An expired passport is genuine -- its fraud score SHOULD be low -- but it
    must never be auto-accepted. Encoding that as a high fraud score would
    misrepresent the evidence; encoding it as blocking states it correctly.
    """

    def test_blocking_failure_prevents_accept(self):
        signals = [
            sig(SignalStatus.PASS, Severity.INFO, code="p1"),
            sig(SignalStatus.FAIL, Severity.MEDIUM, code="expired", blocking=True),
        ]
        result = assess_document(signals, STAGES)
        assert result.band == RiskBand.LOW           # correctly not called fraud
        assert result.decision == Decision.MANUAL_REVIEW  # but not accepted
        assert result.blocking_reasons

    def test_blocking_does_not_inflate_score(self):
        blocking = assess_document(
            [sig(SignalStatus.FAIL, Severity.MEDIUM, blocking=True)], STAGES
        )
        plain = assess_document(
            [sig(SignalStatus.FAIL, Severity.MEDIUM, blocking=False)], STAGES
        )
        assert blocking.score == plain.score

    def test_blocking_signal_that_passed_does_not_block(self):
        result = assess_document(
            [sig(SignalStatus.PASS, Severity.INFO, blocking=True)], STAGES
        )
        assert result.decision == Decision.ACCEPT


class TestEvidenceCoverage:
    def test_thin_evidence_prevents_auto_accept(self):
        """
        A clean score computed from one check is not the same claim as a clean
        score from twenty, and must not be reported as if it were.
        """
        result = assess_document(
            [sig(SignalStatus.PASS, Severity.INFO)],
            expected_stages=(Stage.CLASSIFY, Stage.OCR, Stage.VALIDATE, Stage.FORENSICS),
        )
        assert result.confidence < 0.55
        assert result.decision == Decision.MANUAL_REVIEW

    def test_full_coverage_allows_accept(self):
        signals = [
            signal(
                code=f"ok.{stage.value}",
                stage=stage,
                title="ok",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                reason="ok",
            )
            for stage in (Stage.CLASSIFY, Stage.OCR, Stage.VALIDATE, Stage.FORENSICS)
        ]
        result = assess_document(
            signals,
            expected_stages=(Stage.CLASSIFY, Stage.OCR, Stage.VALIDATE, Stage.FORENSICS),
        )
        assert result.confidence == 1.0
        assert result.decision == Decision.ACCEPT

    def test_high_risk_on_thin_evidence_still_rejects(self):
        """
        Asymmetry by design: we spend reviewer time rather than wrongly clear a
        document, but we do not soften a serious finding because evidence was thin.
        """
        result = assess_document(
            [sig(SignalStatus.FAIL, Severity.CRITICAL)],
            expected_stages=(Stage.CLASSIFY, Stage.OCR, Stage.VALIDATE, Stage.FORENSICS),
        )
        assert result.decision == Decision.REJECT


class TestExplainability:
    def test_every_risk_point_is_attributed(self):
        signals = [
            sig(SignalStatus.FAIL, Severity.HIGH, code="a"),
            sig(SignalStatus.WARN, Severity.MEDIUM, code="b"),
            sig(SignalStatus.PASS, Severity.INFO, code="c"),
        ]
        result = assess_document(signals, STAGES)
        attributed = sum(c["points"] for c in result.contributions)
        assert abs(attributed - result.score) < 0.05

    def test_contributions_ranked_by_impact(self):
        signals = [
            sig(SignalStatus.WARN, Severity.LOW, code="small"),
            sig(SignalStatus.FAIL, Severity.CRITICAL, code="big"),
        ]
        contributions = assess_document(signals, STAGES).contributions
        assert contributions[0]["code"] == "big"

    def test_clean_document_still_explains_itself(self):
        result = assess_document([sig(SignalStatus.PASS, Severity.INFO)], STAGES)
        assert result.top_reasons  # never returns an unexplained verdict
