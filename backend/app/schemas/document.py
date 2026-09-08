"""Document types, extracted fields, and the shape of a verification result."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.signals import Region, Signal


class DocumentType(str, Enum):
    """
    Supported document types.

    UNKNOWN is a first-class outcome, not an error. A classifier that is forced
    to pick one of N known types will confidently mislabel anything it has not
    seen -- and a confidently wrong document type sends the whole downstream
    rule engine off a cliff. Better to say "I don't know, route to a human".
    """

    PASSPORT = "passport"
    VISA = "visa"
    AADHAAR = "aadhaar"
    PAN = "pan"
    DRIVING_LICENCE = "driving_licence"
    VOTER_ID = "voter_id"
    CERTIFICATE = "certificate"
    UNKNOWN = "unknown"


class DocumentSide(str, Enum):
    """
    Which face of a document an image shows.

    Load-bearing, because people photograph both sides and only one of them
    carries identity data. The reverse of an Indian PAN card holds nothing but
    a return address; the back of a marksheet holds grading byelaws. Treating
    those as failed extractions would report a capture that worked perfectly
    as a defective document -- and treating them as sufficient would accept an
    identity that was never actually shown.
    """

    FRONT = "front"
    BACK = "back"
    UNKNOWN = "unknown"


class FieldConfidence(BaseModel):
    """
    A single extracted field with provenance.

    We keep the raw OCR string alongside the normalized value so a reviewer can
    always see what the machine actually read versus what we made of it. When
    normalization fails, that gap is where the answer usually is.
    """

    value: Any = Field(None, description="Normalized, typed value")
    raw: str | None = Field(None, description="Exact text as OCR read it")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = Field("ocr", description="ocr | mrz | barcode | qr | layout | manual")
    region: Region | None = None

    @property
    def present(self) -> bool:
        return self.value is not None and self.value != ""


class ExtractedFields(BaseModel):
    """
    Normalized identity fields, union across all supported document types.

    Deliberately one flat model rather than a class per document type: the
    cross-document consistency engine compares fields ACROSS types, and that
    is far simpler when every document lands in the same shape. Fields that
    don't apply to a given type simply stay unset.
    """

    # Identity
    full_name: FieldConfidence = Field(default_factory=FieldConfidence)
    surname: FieldConfidence = Field(default_factory=FieldConfidence)
    given_names: FieldConfidence = Field(default_factory=FieldConfidence)
    date_of_birth: FieldConfidence = Field(default_factory=FieldConfidence)
    sex: FieldConfidence = Field(default_factory=FieldConfidence)
    father_name: FieldConfidence = Field(default_factory=FieldConfidence)
    address: FieldConfidence = Field(default_factory=FieldConfidence)

    # Document
    document_number: FieldConfidence = Field(default_factory=FieldConfidence)
    nationality: FieldConfidence = Field(default_factory=FieldConfidence)
    issuing_authority: FieldConfidence = Field(default_factory=FieldConfidence)
    issuing_country: FieldConfidence = Field(default_factory=FieldConfidence)
    date_of_issue: FieldConfidence = Field(default_factory=FieldConfidence)
    date_of_expiry: FieldConfidence = Field(default_factory=FieldConfidence)
    place_of_birth: FieldConfidence = Field(default_factory=FieldConfidence)

    # Machine-readable payloads, kept verbatim for audit
    mrz_line1: FieldConfidence = Field(default_factory=FieldConfidence)
    mrz_line2: FieldConfidence = Field(default_factory=FieldConfidence)

    # Full OCR text. Document types whose verifiable content is a TABLE rather
    # than a fixed field set -- marksheets above all -- are validated against
    # this directly, because their redundancy lives in the relationship between
    # values (a total and the same total in words) rather than in any one field.
    raw_text: FieldConfidence = Field(default_factory=FieldConfidence)

    def populated(self) -> dict[str, FieldConfidence]:
        """Only the fields that actually got a value -- for compact display."""
        return {
            name: fc
            for name, fc in self
            if isinstance(fc, FieldConfidence) and fc.present
        }


class RiskBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Decision(str, Enum):
    """
    What the system recommends. Never phrased as 'genuine' or 'fake'.

    VeriShield assesses risk and hands evidence to a human decision-maker;
    it does not pronounce documents authentic. That framing is both more
    honest and more defensible.
    """

    ACCEPT = "accept"
    MANUAL_REVIEW = "manual_review"
    REJECT = "reject"


class RiskAssessment(BaseModel):
    """Output of the Risk Engine: a score, a band, and its full derivation."""

    score: float = Field(..., ge=0.0, le=100.0, description="0 = clean, 100 = maximal risk")
    band: RiskBand
    decision: Decision
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="How complete the evidence was"
    )

    top_reasons: list[str] = Field(
        default_factory=list, description="Ranked plain-language drivers of the score"
    )
    blocking_codes: list[str] = Field(
        default_factory=list,
        description="Signal codes of the blocking failures, so a caller can "
        "decide one has been answered elsewhere without matching prose.",
    )
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Hard validity failures that prevent acceptance regardless "
        "of the fraud score, e.g. an expired document. Reported separately "
        "because 'authentic but unusable' is a different finding from "
        "'possibly forged', and a reviewer must be able to tell them apart.",
    )
    contributions: list[dict[str, Any]] = Field(
        default_factory=list, description="Per-signal risk points, descending"
    )
    coverage: dict[str, str] = Field(
        default_factory=dict, description="Stage -> ran | skipped | errored"
    )


class DocumentAnalysis(BaseModel):
    """Everything the pipeline learned about ONE document."""

    document_id: str
    filename: str
    document_type: DocumentType = DocumentType.UNKNOWN
    type_confidence: float = 0.0
    side: DocumentSide = DocumentSide.UNKNOWN

    fields: ExtractedFields = Field(default_factory=ExtractedFields)
    signals: list[Signal] = Field(default_factory=list)
    risk: RiskAssessment | None = None

    image_width: int | None = None
    image_height: int | None = None
    face_region: Region | None = None

    processing_ms: dict[str, float] = Field(
        default_factory=dict, description="Stage -> wall time, for the perf panel"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VerificationResult(BaseModel):
    """
    Result of a whole verification CASE -- one or more documents plus an
    optional presented face, assessed together.

    The case-level risk is not simply the max of the per-document risks:
    cross-document inconsistency is risk that exists only at this level,
    which is exactly what Layer 7 contributes.
    """

    case_id: str
    documents: list[DocumentAnalysis] = Field(default_factory=list)
    cross_document_signals: list[Signal] = Field(default_factory=list)
    overall_risk: RiskAssessment | None = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @property
    def total_ms(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds() * 1000
