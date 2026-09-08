"""
End-to-end pipeline and cross-document tests.

These use synthetic images and injected OCR text, so they run without the ML
stack installed and without any real identity document.
"""

from __future__ import annotations

from datetime import date

import cv2
import numpy as np
import pytest

from app.pipeline.orchestrator import analyze_document, verify_case
from app.pipeline.stages.classify import classify_text
from app.pipeline.stages.cross_document import compare_documents, names_match
from app.pipeline.stages.ocr import InjectedTextProvider
from app.rules.mrz import check_digit
from app.schemas.document import Decision, DocumentType
from app.schemas.signals import SignalStatus

MRZ_L1 = "P<INDSUMAN<<RAJDEEP<<<<<<<<<<<<<<<<<<<<<<<<<"


def make_mrz_line2(num="Z3456789", nat="IND", dob="030412", sex="M", exp="330411", pn=""):
    """Build a TD3 line 2 with correct check digits."""
    n9, p14 = num.ljust(9, "<"), pn.ljust(14, "<")
    body = (
        n9 + str(check_digit(n9)) + nat + dob + str(check_digit(dob))
        + sex + exp + str(check_digit(exp)) + p14 + str(check_digit(p14))
    )
    return body + str(check_digit(body[0:10] + body[13:20] + body[21:43]))


@pytest.fixture
def document_image() -> bytes:
    """A plain synthetic document image, JPEG encoded."""
    img = np.full((520, 820, 3), 238, np.uint8)
    cv2.rectangle(img, (0, 0), (819, 70), (150, 40, 40), -1)
    cv2.putText(img, "REPUBLIC OF INDIA", (24, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return enc.tobytes()


def passport_text(line2: str) -> str:
    return f"REPUBLIC OF INDIA\nPASSPORT\nPLACE OF ISSUE PATNA\n{MRZ_L1}\n{line2}"


class TestClassification:
    def test_passport_identified_from_mrz(self):
        doc_type, conf, _ = classify_text(passport_text(make_mrz_line2()))
        assert doc_type == DocumentType.PASSPORT
        assert conf >= 0.45

    def test_pan_identified(self):
        doc_type, _, _ = classify_text(
            "INCOME TAX DEPARTMENT\nPERMANENT ACCOUNT NUMBER\nABCDE1234F"
        )
        assert doc_type == DocumentType.PAN

    def test_unrecognised_text_returns_unknown(self):
        doc_type, _, _ = classify_text("shopping list\nmilk\nbread\neggs")
        assert doc_type == DocumentType.UNKNOWN

    def test_empty_text_returns_unknown(self):
        assert classify_text("")[0] == DocumentType.UNKNOWN


class TestSingleDocumentPipeline:
    def test_clean_passport_is_accepted(self, document_image):
        result = analyze_document(
            document_image,
            "passport.jpg",
            ocr_provider=InjectedTextProvider(passport_text(make_mrz_line2())),
        )
        assert result.document_type == DocumentType.PASSPORT
        assert result.risk.decision == Decision.ACCEPT
        assert result.risk.band.value == "low"

    def test_tampered_passport_is_rejected(self, document_image):
        good = make_mrz_line2()
        tampered = good[:13] + "990412" + good[19:]  # DOB edited, digits stale
        result = analyze_document(
            document_image,
            "passport.jpg",
            ocr_provider=InjectedTextProvider(passport_text(tampered)),
        )
        assert result.risk.decision == Decision.REJECT
        assert any(
            s.code.startswith("mrz.checksum") and s.status == SignalStatus.FAIL
            for s in result.signals
        )

    def test_expired_passport_escalates_without_being_called_fraud(self, document_image):
        result = analyze_document(
            document_image,
            "passport.jpg",
            ocr_provider=InjectedTextProvider(passport_text(make_mrz_line2(exp="200411"))),
        )
        assert result.risk.decision == Decision.MANUAL_REVIEW
        assert result.risk.band.value == "low"      # not accused of forgery
        assert result.risk.blocking_reasons          # but blocked from acceptance

    def test_unknown_document_type_does_not_claim_a_rulebook_ran(self, document_image):
        result = analyze_document(
            document_image,
            "mystery.jpg",
            ocr_provider=InjectedTextProvider("some unrelated text about nothing"),
        )
        assert result.document_type == DocumentType.UNKNOWN
        assert any(s.code == "validate.no_rulebook" for s in result.signals)

    def test_broken_image_does_not_crash_the_pipeline(self):
        result = analyze_document(b"this is not an image", "broken.jpg")
        assert result.risk is not None
        assert any(s.status == SignalStatus.ERROR for s in result.signals)

    def test_every_stage_is_timed(self, document_image):
        result = analyze_document(
            document_image,
            "passport.jpg",
            ocr_provider=InjectedTextProvider(passport_text(make_mrz_line2())),
        )
        for stage in ("ingest", "ocr", "classify", "extract", "validate", "forensics"):
            assert stage in result.processing_ms


class TestNameMatching:
    """
    Indian identity documents legitimately disagree about names. Flagging that
    variation as fraud would produce false accusations against the people least
    able to contest them, so these cases must NOT be treated as mismatches.
    """

    @pytest.mark.parametrize(
        "a,b",
        [
            ("RAJDEEP SUMAN", "SUMAN RAJDEEP"),        # passport vs PAN ordering
            ("RAJDEEP SUMAN", "Rajdeep Suman"),        # case
            ("SHRI RAJDEEP SUMAN", "RAJDEEP SUMAN"),   # honorific
            ("RAJDEEP  SUMAN", "RAJDEEP SUMAN"),       # whitespace
            ("RAJDEEP KUMAR SUMAN", "RAJDEEP SUMAN"),  # dropped middle name
        ],
    )
    def test_benign_variation_matches(self, a, b):
        matched, score = names_match(a, b)
        assert matched, f"{a!r} vs {b!r} scored only {score:.0f}"

    @pytest.mark.parametrize(
        "a,b",
        [
            ("RAJDEEP SUMAN", "PRIYA SHARMA"),
            ("RAJDEEP SUMAN", "AMIT KUMAR"),
        ],
    )
    def test_different_people_do_not_match(self, a, b):
        matched, _ = names_match(a, b)
        assert not matched


class TestCrossDocument:
    def _doc(self, name: str, dob: date, doc_type=DocumentType.PASSPORT):
        from app.schemas.document import DocumentAnalysis, FieldConfidence

        doc = DocumentAnalysis(document_id="x", filename="f", document_type=doc_type)
        doc.fields.full_name = FieldConfidence(value=name, raw=name, confidence=1.0)
        doc.fields.date_of_birth = FieldConfidence(value=dob, raw=str(dob), confidence=1.0)
        return doc

    def test_single_document_reports_check_as_skipped(self):
        signals = compare_documents([self._doc("RAJDEEP SUMAN", date(2003, 4, 12))])
        assert len(signals) == 1
        assert signals[0].status == SignalStatus.SKIP

    def test_consistent_documents_pass(self):
        docs = [
            self._doc("RAJDEEP SUMAN", date(2003, 4, 12), DocumentType.PASSPORT),
            self._doc("SUMAN RAJDEEP", date(2003, 4, 12), DocumentType.PAN),
        ]
        signals = compare_documents(docs)
        assert all(s.status == SignalStatus.PASS for s in signals)

    def test_dob_mismatch_is_flagged(self):
        docs = [
            self._doc("RAJDEEP SUMAN", date(2003, 4, 12), DocumentType.PASSPORT),
            self._doc("RAJDEEP SUMAN", date(1999, 4, 12), DocumentType.PAN),
        ]
        signals = compare_documents(docs)
        assert any(
            s.code == "cross.dob.mismatch" and s.status == SignalStatus.FAIL
            for s in signals
        )

    def test_january_first_placeholder_is_not_treated_as_fraud(self):
        """
        1 January is the standard placeholder when only the birth year is
        known. Treating it as a contradiction would penalise older applicants
        whose exact birth date was never recorded.
        """
        docs = [
            self._doc("RAJDEEP SUMAN", date(2003, 4, 12), DocumentType.PASSPORT),
            self._doc("RAJDEEP SUMAN", date(2003, 1, 1), DocumentType.PAN),
        ]
        signals = compare_documents(docs)
        dob_signals = [s for s in signals if s.code.startswith("cross.dob")]
        assert dob_signals[0].status == SignalStatus.WARN
        assert dob_signals[0].severity.value == "low"

    def test_name_mismatch_is_flagged(self):
        docs = [
            self._doc("RAJDEEP SUMAN", date(2003, 4, 12), DocumentType.PASSPORT),
            self._doc("PRIYA SHARMA", date(2003, 4, 12), DocumentType.PAN),
        ]
        signals = compare_documents(docs)
        assert any(
            s.code == "cross.name.mismatch" and s.status == SignalStatus.FAIL
            for s in signals
        )


class TestCaseVerification:
    def test_two_clean_consistent_documents_accepted(self, document_image):
        line2 = make_mrz_line2()
        result = verify_case(
            [(document_image, "a.jpg"), (document_image, "b.jpg")],
            ocr_provider=InjectedTextProvider(passport_text(line2)),
        )
        assert result.overall_risk.decision == Decision.ACCEPT
        assert len(result.documents) == 2

    def test_case_risk_is_not_diluted_by_averaging(self, document_image):
        """
        One bad document in a case must not be averaged away by good ones.
        Case risk takes the worst document as its floor.
        """
        good = make_mrz_line2()
        bad = good[:13] + "990412" + good[19:]

        clean = analyze_document(
            document_image, "a.jpg", ocr_provider=InjectedTextProvider(passport_text(good))
        )
        tampered = analyze_document(
            document_image, "b.jpg", ocr_provider=InjectedTextProvider(passport_text(bad))
        )

        from app.risk.engine import assess_case

        case = assess_case([clean.risk, tampered.risk], [])
        assert case.score >= tampered.risk.score
        assert case.decision == Decision.REJECT


class TestPassportWithoutMRZ:
    """
    Regression from real data.

    A real Indian passport photograph whose MRZ band was not captured scored
    22/100 and was ACCEPTED -- despite the pipeline having verified nothing
    about its contents, since every passport content check reads from the MRZ.
    """

    def test_passport_without_mrz_is_not_auto_accepted(self, document_image):
        result = analyze_document(
            document_image,
            "passport.jpg",
            declared_type=DocumentType.PASSPORT,
            ocr_provider=InjectedTextProvider(
                "REPUBLIC OF INDIA\nPASSPORT\nPLACE OF ISSUE PATNA"
            ),
        )
        assert result.risk.decision != Decision.ACCEPT
        assert any(s.code == "mrz.not_found" for s in result.signals)
        assert result.risk.blocking_reasons

    def test_missing_mrz_is_not_scored_as_fraud(self, document_image):
        """A cropped scan is a capture problem, not evidence of forgery."""
        result = analyze_document(
            document_image,
            "passport.jpg",
            declared_type=DocumentType.PASSPORT,
            ocr_provider=InjectedTextProvider(
                "REPUBLIC OF INDIA\nPASSPORT\nPLACE OF ISSUE PATNA"
            ),
        )
        assert result.risk.band.value == "low"


class TestClassifierEvidenceInvariant:
    """
    No identifier SHAPE may decide a document type on its own.

    Five letters, four digits, a letter. A twelve-digit run. These are exactly
    what OCR noise produces by accident on any densely printed page, so a
    document must also show the issuer's printed wording before a type is
    assigned. Getting this wrong is not a cosmetic mislabel: the type selects
    the rulebook, so a document classified from a hallucinated string is
    validated against rules that were never meant for it.

    This was a live defect -- the PAN number pattern carried 0.55 against a
    0.45 threshold, so one fabricated string classified any document as a PAN
    card with no corroboration whatsoever.
    """

    def test_no_bare_identifier_pattern_can_classify_alone(self):
        from app.pipeline.stages.classify import MIN_TYPE_CONFIDENCE, _RULES

        offenders = []
        for doc_type, rules in _RULES.items():
            for pattern, weight, cue in rules:
                # Identifier patterns are the ones with no alphabetic literal
                # in them -- they match a shape, not printed words.
                looks_like_a_shape = not any(
                    c.isalpha() for c in pattern.pattern.replace("A-Z", "").replace("\b", "")
                )
                if looks_like_a_shape and weight >= MIN_TYPE_CONFIDENCE:
                    offenders.append(f"{doc_type.value}: {cue} at {weight}")

        assert not offenders, (
            "these identifier patterns can classify a document by themselves: "
            + "; ".join(offenders)
        )

    @pytest.mark.parametrize(
        "text",
        [
            "SOME DOCUMENT\nEJAPS0276M\nrandom text",       # PAN shape
            "SOME DOCUMENT\n1234 5678 9012\nrandom text",   # Aadhaar shape
            "SOME DOCUMENT\nMH12 2019 0001234",             # DL shape
            "SOME DOCUMENT\nABC1234567",                    # EPIC shape
        ],
    )
    def test_identifier_shape_without_issuer_text_is_unknown(self, text):
        assert classify_text(text)[0] == DocumentType.UNKNOWN

    def test_issuer_wording_still_classifies_confidently(self):
        """The fix must not have cost us the real signal."""
        real_pan = (
            "INCOME TAX DEPARTMENT\nGOVT.OF INDIA\n"
            "Permanent Account Number\nEJAPS0276M"
        )
        doc_type, confidence, _ = classify_text(real_pan)
        assert doc_type == DocumentType.PAN
        assert confidence >= 0.9


class TestClassifierOnDegradedOCR:
    """
    Real OCR output from real Aadhaar cards, kept verbatim.

    Exact-match rules recognised only 46 of 217 real cards. The rest came back
    as "Govemment of India", "GOVERNIENT OFINCAA", "/Your Aadhar No." -- genuine
    documents whose printed wording OCR mangled. Demanding exact spelling threw
    away three quarters of the set and sent them all to manual review.
    """

    # Verbatim PaddleOCR output from four different real Aadhaar cards.
    MANGLED = [
        "ANESTNYT /Your Aadhar No. 87701802 0033 7 HR/Your Aar No 333 "
        "8770 18020033 Gorenmentdinda",
        "600312479950 3-33 H R Gavernment of India Gomatil /D080105/1993 "
        "T/Female 60031247 9950 333",
        "3 g4/MALE f/DOB01/01/1976 Bholanath Govemment of India 865093562671",
    ]

    @pytest.mark.parametrize("text", MANGLED)
    def test_ocr_corrupted_aadhaar_is_still_recognised(self, text):
        assert classify_text(text)[0] == DocumentType.AADHAAR

    def test_shared_wording_alone_decides_nothing(self):
        """
        'Government of India' is printed on Aadhaar cards, PAN cards and
        passports alike. It may corroborate a type that other evidence already
        points at; it must never pick one on its own.
        """
        doc_type, confidence, _ = classify_text(
            "GOVERNMENT OF INDIA some official looking document"
        )
        assert doc_type == DocumentType.UNKNOWN
        assert confidence < 0.45

    def test_identifier_plus_shared_wording_does_classify(self):
        """
        The combination is legitimate evidence even though neither half is.
        Several weak cues agreeing is a different thing from one fabricated
        pattern deciding alone -- which is the failure the weight cap prevents.
        """
        assert (
            classify_text("Government of India 8770 1802 0033 MALE DOB 01/01/1976")[0]
            == DocumentType.AADHAAR
        )

    def test_fuzzy_matching_does_not_confuse_pan_with_aadhaar(self):
        real_pan = (
            "HR INCOME TAX DEPARTMENT GOVT.OF INDIA MONIKA MAHADEV SHINDE "
            "Permanent Account Number EJAPS0276M"
        )
        assert classify_text(real_pan)[0] == DocumentType.PAN

    def test_evidence_records_that_a_match_was_approximate(self):
        """A reviewer must be able to see the cue was fuzzy, not exact."""
        _, _, evidence = classify_text(self.MANGLED[1])
        assert any("approximate" in e.cue for e in evidence)
