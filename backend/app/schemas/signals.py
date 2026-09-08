"""
The Signal contract.

Every pipeline stage emits Signals -- never bare booleans, never a raw score.
A Signal is a single piece of evidence about a document, carrying both the
machine-readable verdict and the human-readable reason for it.

This is the backbone of VeriShield's explainability: the Risk Engine consumes
ONLY Signals, so any score it produces can always be traced back to the
specific evidence that produced it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Stage(str, Enum):
    """Pipeline stage that produced a signal. Mirrors the 8-layer architecture."""

    INGEST = "ingest"
    CLASSIFY = "classify"
    OCR = "ocr"
    EXTRACT = "extract"
    VALIDATE = "validate"
    FORENSICS = "forensics"
    FACE = "face"
    CROSS_DOC = "cross_doc"
    DATABASE = "database"


class SignalStatus(str, Enum):
    """
    Verdict of a single check.

    PASS  -- the check ran and the document satisfied it.
    WARN  -- the check ran, result is suspicious but not disqualifying.
    FAIL  -- the check ran and the document failed it.
    SKIP  -- the check did not apply (e.g. no face on a PAN card).
    ERROR -- the check could not run (model unavailable, corrupt input).

    SKIP and ERROR are deliberately distinct. A skipped check is not evidence
    of anything; an errored check means we are missing evidence we expected,
    and the Risk Engine surfaces that as reduced confidence rather than
    silently treating it as a pass.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class Severity(str, Enum):
    """How much a FAIL on this signal should matter."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Severity -> risk weight. Tuned in one place so the whole scoring model is
# auditable at a glance, which is the point of a transparent risk engine.
SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.INFO: 0.0,
    Severity.LOW: 5.0,
    Severity.MEDIUM: 12.0,
    Severity.HIGH: 25.0,
    Severity.CRITICAL: 40.0,
}


class Signal(BaseModel):
    """
    One piece of evidence about a document.

    `confidence` is how sure we are of the verdict itself (0-1), NOT how good
    the document is. A FAIL at confidence 0.99 is strong evidence of a problem;
    a FAIL at confidence 0.4 is a reason to ask a human.
    """

    code: str = Field(..., description="Stable machine ID, e.g. 'mrz.checksum.dob'")
    stage: Stage
    title: str = Field(..., description="Short human label, e.g. 'MRZ DOB checksum'")
    status: SignalStatus
    severity: Severity = Severity.MEDIUM
    confidence: float = Field(1.0, ge=0.0, le=1.0)

    reason: str = Field(
        ...,
        description="Plain-language explanation shown to the human reviewer. "
        "Must state what was checked and what was found.",
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured backing data: expected vs actual, coordinates, "
        "raw values. Rendered in the dashboard's evidence drawer.",
    )
    regions: list[Region] = Field(
        default_factory=list,
        description="Image regions this signal refers to, for overlay rendering.",
    )

    blocking: bool = Field(
        False,
        description="If this signal FAILs or ERRORs, the document cannot be "
        "ACCEPTed regardless of how low the fraud risk score is.",
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_blocking_failure(self) -> bool:
        """
        A hard gate that did not produce the evidence acceptance requires.

        Separates two questions the risk score alone cannot distinguish:
        "is this document fraudulent?" and "is this document acceptable?"
        An expired passport is almost certainly genuine -- its fraud score
        SHOULD be low -- but it must never be auto-accepted. Encoding that as
        a high fraud score would be a lie about the evidence; encoding it as
        a blocking condition states the truth: authentic, but not usable.

        ERROR blocks as well as FAIL, and the distinction between them matters
        less here than what they have in common: neither produced the
        affirmative evidence a clean acceptance rests on. A passport whose MRZ
        could not be read is not accused of anything -- but nothing about its
        contents was verified either, so approving it would mean approving a
        document the system never actually checked.

        SKIP deliberately does not block. A check that did not apply is not a
        gap in the evidence; it is a check that was never owed.
        """
        return self.blocking and self.status in (
            SignalStatus.FAIL,
            SignalStatus.ERROR,
        )

    @property
    def weight(self) -> float:
        """Risk points this signal contributes when it fails, before confidence."""
        return SEVERITY_WEIGHT[self.severity]

    @property
    def contribution(self) -> float:
        """
        Actual risk points contributed to the final score.

        Only adverse outcomes contribute risk. A WARN contributes half of a
        FAIL: it is a real concern but not a determination. ERROR contributes
        a small amount because missing evidence is itself mildly suspicious
        -- but far less than evidence of a problem.
        """
        if self.status == SignalStatus.FAIL:
            return self.weight * self.confidence
        if self.status == SignalStatus.WARN:
            return self.weight * self.confidence * 0.5
        if self.status == SignalStatus.ERROR:
            return self.weight * 0.15
        return 0.0


class Region(BaseModel):
    """
    An axis-aligned box on the source image, in absolute pixel coordinates.

    Used to draw the "why did AI flag this" overlay -- the demo feature that
    turns a score into visible evidence.
    """

    x: int
    y: int
    width: int
    height: int
    label: str | None = None
    suspicion: float | None = Field(
        None, ge=0.0, le=1.0, description="0-1 tampering suspicion for heatmap tint"
    )

    def as_xyxy(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


# Region is referenced by Signal before it is defined, so rebuild the model.
Signal.model_rebuild()


def signal(
    code: str,
    stage: Stage,
    title: str,
    status: SignalStatus,
    reason: str,
    *,
    severity: Severity = Severity.MEDIUM,
    confidence: float = 1.0,
    evidence: dict[str, Any] | None = None,
    regions: list[Region] | None = None,
    blocking: bool = False,
) -> Signal:
    """Terse constructor -- pipeline stages emit a lot of these."""
    return Signal(
        code=code,
        stage=stage,
        title=title,
        status=status,
        severity=severity,
        confidence=confidence,
        reason=reason,
        evidence=evidence or {},
        regions=regions or [],
        blocking=blocking,
    )
