"""
Tests for the layers added last: face verification (L6), the authority
registry (L8), and the audit trail.

None of these load a model or need a running database. The behaviour that
matters here is what the system says when a capability is ABSENT, and getting
that wrong is how a verification service quietly stops verifying while still
returning confident answers.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.pipeline.stages.face import (
    POSSIBLE_MATCH_THRESHOLD,
    STRONG_MATCH_THRESHOLD,
    DetectedFace,
    FaceResult,
    NullFaceProvider,
    OpenCVFaceProvider,
    cosine_similarity,
    verify_faces,
)
from app.registry.authority import (
    LookupResult,
    MatchOutcome,
    NullRegistry,
    RegistryRecord,
    SyntheticRegistry,
    check_registry,
)
from app.schemas.document import DocumentType, ExtractedFields, FieldConfidence
from app.schemas.signals import Region, SignalStatus
from app.storage.audit import _redact, document_fingerprint


def fields_with(**kwargs) -> ExtractedFields:
    f = ExtractedFields()
    for name, value in kwargs.items():
        setattr(f, name, FieldConfidence(value=value, raw=str(value), confidence=0.9))
    return f


# ------------------------------------------------------------------ Face --


class TestFaceProviderHonesty:
    def test_opencv_provider_does_not_claim_recognition(self):
        """
        It can find a portrait. It cannot tell one person from another, and a
        pixel or histogram comparison dressed up as a similarity score would be
        read as identity evidence when it measures lighting and pose.
        """
        provider = OpenCVFaceProvider()
        assert provider.detection_available is True
        assert provider.recognition_available is False

    def test_missing_recognition_blocks_rather_than_guessing(self):
        signals = verify_faces(b"doc", b"selfie", NullFaceProvider())
        assert len(signals) == 1
        assert signals[0].status == SignalStatus.ERROR
        assert signals[0].blocking is True

    def test_missing_recognition_says_what_was_not_checked(self):
        reason = verify_faces(b"doc", b"selfie", NullFaceProvider())[0].reason
        assert "NOT checked" in reason or "not checked" in reason.lower()


class TestFaceThresholds:
    def test_uncertain_band_exists_between_the_thresholds(self):
        """
        Faces vary with age, lighting and pose. A system that always answers
        yes or no will be confidently wrong about real people at both ends, so
        the middle routes to a human instead.
        """
        assert POSSIBLE_MATCH_THRESHOLD < STRONG_MATCH_THRESHOLD

    def test_cosine_similarity_of_identical_vectors_is_one(self):
        v = np.array([0.1, 0.5, -0.3, 0.8])
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_cosine_similarity_handles_zero_vector(self):
        zero = np.zeros(4)
        assert cosine_similarity(zero, np.array([1.0, 0, 0, 0])) == 0.0

    @pytest.mark.parametrize(
        "similarity,expected_code",
        [
            (0.90, "face.match.strong"),
            (0.35, "face.match.uncertain"),
            (0.05, "face.match.mismatch"),
        ],
    )
    def test_three_outcomes_not_two(self, similarity, expected_code, monkeypatch):
        """A verdict, a non-verdict, and a refusal -- the middle one is the point."""

        class FakeProvider:
            name = "fake"
            detection_available = True
            recognition_available = True

            def __init__(self, vector):
                self._vector = vector

            def analyze(self, image_bytes):
                vec = np.array([1.0, 0.0]) if image_bytes == b"doc" else self._vector
                return FaceResult(
                    faces=[
                        DetectedFace(
                            region=Region(x=0, y=0, width=200, height=200),
                            confidence=0.9,
                            embedding=vec,
                        )
                    ],
                    engine="fake",
                    detection_available=True,
                    recognition_available=True,
                )

        angle = np.arccos(np.clip(similarity, -1, 1))
        provider = FakeProvider(np.array([np.cos(angle), np.sin(angle)]))
        signals = verify_faces(b"doc", b"selfie", provider)
        assert signals[0].code == expected_code

    def test_uncertain_result_blocks_acceptance(self):
        """Undecided is not the same as fine."""

        class FakeProvider:
            name = "fake"
            detection_available = True
            recognition_available = True

            def analyze(self, image_bytes):
                vec = (
                    np.array([1.0, 0.0])
                    if image_bytes == b"doc"
                    else np.array([0.35, np.sqrt(1 - 0.35**2)])
                )
                return FaceResult(
                    faces=[
                        DetectedFace(
                            region=Region(x=0, y=0, width=200, height=200),
                            confidence=0.9,
                            embedding=vec,
                        )
                    ],
                    engine="fake",
                    detection_available=True,
                    recognition_available=True,
                )

        signal_ = verify_faces(b"doc", b"selfie", FakeProvider())[0]
        assert signal_.code == "face.match.uncertain"
        assert signal_.blocking is True


# -------------------------------------------------------------- Registry --


class TestRegistryNonAuthoritative:
    """
    The rule this module exists to get right.

    A registry lookup that returns "no match" reads exactly like fraud. A
    synthetic registry holding five records knows nothing about anybody else,
    so treating its silence as evidence would reject essentially every real
    document presented to it.
    """

    def setup_method(self):
        self.registry = SyntheticRegistry(
            [
                RegistryRecord(
                    document_type="aadhaar",
                    document_number="234123412346",
                    full_name="DEMO HOLDER",
                    date_of_birth="1995-06-15",
                ),
                RegistryRecord(
                    document_type="aadhaar",
                    document_number="999999990019",
                    full_name="REVOKED HOLDER",
                    status="revoked",
                    reason_code="reported_lost",
                ),
            ]
        )

    def test_not_found_in_synthetic_registry_is_skip_not_failure(self):
        signals = check_registry(
            DocumentType.AADHAAR, fields_with(document_number="597191165539"), self.registry
        )
        assert signals[0].status == SignalStatus.SKIP
        assert signals[0].code == "registry.not_found_non_authoritative"

    def test_not_found_contributes_no_risk(self):
        signals = check_registry(
            DocumentType.AADHAAR, fields_with(document_number="597191165539"), self.registry
        )
        assert sum(s.contribution for s in signals) == 0.0

    def test_a_hit_in_a_synthetic_registry_carries_low_confidence(self):
        """A green tick here must not imply the issuer confirmed anything."""
        signals = check_registry(
            DocumentType.AADHAAR,
            fields_with(document_number="234123412346", full_name="DEMO HOLDER"),
            self.registry,
        )
        match = next(s for s in signals if s.code == "registry.match")
        assert match.confidence <= 0.5
        assert "not the issuing authority" in match.reason

    def test_not_found_from_an_authoritative_source_IS_a_failure(self):
        """The distinction the whole design turns on."""

        class AuthoritativeRegistry:
            name = "issuer-api"
            authoritative = True

            def lookup(self, doc_type, number, fields):
                return LookupResult(
                    outcome=MatchOutcome.NOT_FOUND,
                    provider=self.name,
                    authoritative=True,
                )

        signals = check_registry(
            DocumentType.AADHAAR,
            fields_with(document_number="597191165539"),
            AuthoritativeRegistry(),
        )
        assert signals[0].status == SignalStatus.FAIL
        assert signals[0].severity.value == "critical"


class TestRegistryOutcomes:
    def setup_method(self):
        self.registry = SyntheticRegistry(
            [
                RegistryRecord(
                    document_type="aadhaar",
                    document_number="234123412346",
                    full_name="DEMO HOLDER",
                    date_of_birth="1995-06-15",
                ),
                RegistryRecord(
                    document_type="aadhaar",
                    document_number="999999990019",
                    full_name="REVOKED HOLDER",
                    status="revoked",
                    reason_code="reported_lost",
                ),
            ]
        )

    def test_revoked_document_blocks(self):
        signals = check_registry(
            DocumentType.AADHAAR, fields_with(document_number="999999990019"), self.registry
        )
        assert signals[0].code == "registry.revoked"
        assert signals[0].blocking is True

    def test_field_mismatch_is_reported(self):
        signals = check_registry(
            DocumentType.AADHAAR,
            fields_with(document_number="234123412346", full_name="SOMEBODY ELSE"),
            self.registry,
        )
        assert signals[0].code == "registry.field_mismatch"

    def test_name_comparison_tolerates_transliteration(self):
        """Reuses the cross-document matcher, for the reasons documented there."""
        signals = check_registry(
            DocumentType.AADHAAR,
            fields_with(document_number="234123412346", full_name="HOLDER DEMO"),
            self.registry,
        )
        assert signals[0].code == "registry.match"

    def test_no_registry_configured_is_skip(self):
        signals = check_registry(
            DocumentType.PAN, fields_with(document_number="PQZPK6899H"), NullRegistry()
        )
        assert signals[0].status == SignalStatus.SKIP

    def test_aadhaar_number_is_masked_in_output(self):
        signals = check_registry(
            DocumentType.AADHAAR, fields_with(document_number="234123412346"), self.registry
        )
        for s in signals:
            assert "234123412346" not in s.reason


# ----------------------------------------------------------------- Audit --


class TestAuditRedaction:
    def test_document_numbers_are_masked_before_storage(self):
        redacted = _redact({"document_number": "597191165539"})
        assert redacted["document_number"].endswith("5539")
        assert "5971" not in redacted["document_number"]

    def test_redaction_reaches_nested_structures(self):
        redacted = _redact(
            {"signals": [{"evidence": {"document_number": "PQZPK6899H"}}]}
        )
        assert redacted["signals"][0]["evidence"]["document_number"].endswith("899H")

    def test_names_are_kept(self):
        """A reviewer auditing a decision needs to know whose document it was."""
        assert _redact({"full_name": "SOMEONE REAL"})["full_name"] == "SOMEONE REAL"

    def test_fingerprint_is_stable_and_content_addressed(self):
        assert document_fingerprint(b"abc") == document_fingerprint(b"abc")
        assert document_fingerprint(b"abc") != document_fingerprint(b"abd")
