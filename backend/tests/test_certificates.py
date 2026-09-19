"""
Certificates as they were actually collected: 4 board marksheets, 8 course,
internship, participation, training and award certificates.

Pinned here:
  - the second kind is recognised, and sent to a person with what to check
    instead of passing marksheet rules it was never subject to;
  - the marksheet totals check compares each total with its own row -- it used
    to compare a repeated total with the first row that spelled it;
  - a certificate's QR link is reported, never fetched, and a redirect is named;
  - identifiers taken from certificates are masked in the audit trail.

All text is made up; no real certificate is used.
"""

from __future__ import annotations

from app.pipeline import orchestrator
from app.pipeline.stages.classify import classify_text
from app.rules import aadhaar_qr
from app.rules.certificate import certificate_kind, validate_certificate
from app.schemas.document import DocumentType, ExtractedFields, FieldConfidence
from app.schemas.signals import SignalStatus
from app.storage.audit import _redact


def text_fields(text: str) -> ExtractedFields:
    fields = ExtractedFields()
    fields.raw_text = FieldConfidence(value=text, raw=text, confidence=0.9)
    return fields


def codes(signals) -> set[str]:
    return {s.code for s in signals}


INTERNSHIP = (
    "CERTIFICATE OF INTERNSHIP\nThis is to certify that\nASHA VERMA\n"
    "has successfully completed a four-week internship in web development\n2024"
)


# ------------------------------------------------------------- the kinds --


class TestCredentialCertificates:
    def test_a_course_certificate_is_told_apart_from_a_marksheet(self):
        assert certificate_kind(INTERNSHIP) == "credential"

    def test_a_marksheet_that_also_says_awarded_stays_a_marksheet(self):
        # A board statement is "awarded" and "certified" too; its table decides.
        text = (
            "CENTRAL BOARD OF SECONDARY EDUCATION\nMARKS OBTAINED\n"
            "This is to certify that the candidate has been awarded"
        )
        assert certificate_kind(text) == "marksheet"

    def test_it_is_withheld_for_a_person_not_passed_on_nothing(self):
        signals = validate_certificate(None, text_fields(INTERNSHIP))
        blocked = next(s for s in signals if s.code == "certificate.credential.unverifiable")
        assert blocked.is_blocking_failure
        assert blocked.severity.value == "low"  # unverified, not accused
        # The marksheet rules are not applied to it at all.
        assert "certificate.issuer.unknown" not in codes(signals)
        assert not {c for c in codes(signals) if c.startswith("certificate.totals")}

    def test_what_it_offers_for_checking_is_pointed_at(self):
        text = INTERNSHIP + "\nCertificate ID: CA-2024-77812\nVerify at https://www.example-academy.org/verify"
        signals = validate_certificate(None, text_fields(text))
        reference = next(s for s in signals if s.code == "certificate.reference.found")
        assert reference.evidence["domains"] == ["example-academy.org"]
        assert reference.evidence["certificate_id_printed"] is True
        # The ID itself is not repeated anywhere in the result.
        assert all("77812" not in s.reason and "77812" not in str(s.evidence) for s in signals)

    def test_a_future_year_is_still_caught(self):
        from datetime import date

        text = INTERNSHIP.replace("2024", str(date.today().year + 2))
        assert "certificate.year.future" in codes(validate_certificate(None, text_fields(text)))


class TestClassifyingCertificates:
    def test_a_participation_certificate_from_a_ministry(self):
        doc_type, _, evidence = classify_text(
            "CERTIFICATE OF PARTICIPATION\nMINISTRYOF COMMERCE AND INDUSTRY\n"
            "for participating in the national quiz"
        )
        assert doc_type == DocumentType.CERTIFICATE
        assert {e.cue for e in evidence} >= {"certificate wording", "issuing institution"}

    def test_a_course_completion_certificate(self):
        assert classify_text(INTERNSHIP)[0] == DocumentType.CERTIFICATE

    def test_certificate_wording_alone_decides_nothing(self):
        assert classify_text("has successfully completed the course")[0] == DocumentType.UNKNOWN


# ------------------------------------------------------- the totals check --


class TestTotalsPosition:
    def test_a_total_repeated_from_an_earlier_row_is_compared_with_its_own_row(self):
        """
        Regression from a genuine marksheet: HINDI's total SEVENTY was located
        by searching for "SEVENTY", which found ENGLISH's SEVENTY THREE first,
        and 70 was reported as disagreeing with 73.
        """
        text = "CBSE\nENGLISH 301 073 SEVENTY THREE\nHINDI 002 070 SEVENTY"
        signals = validate_certificate(None, text_fields(text))
        assert "certificate.totals.mismatch" not in codes(signals)
        assert next(s for s in signals if s.code == "certificate.totals.match").evidence["verified"]

    def test_a_figure_printed_just_after_its_words_is_accepted(self):
        # OCR does not always keep the table's reading order.
        text = "CBSE\nENGLISH 301\nSEVENTY EIGHT\n078"
        assert "certificate.totals.match" in codes(validate_certificate(None, text_fields(text)))

    def test_a_changed_mark_is_not_rescued_by_a_figure_further_down(self):
        # Digits raised to 93; the words still say 63. The next row happens to
        # print 63 -- but beyond the single figure right after, so it is not used.
        text = "CBSE\nENGLISH 301 093 SIXTY THREE A1\nHINDI 002 071 SEVENTY ONE\nMATHS 041 063 SIXTY THREE"
        signals = validate_certificate(None, text_fields(text))
        assert "certificate.totals.mismatch" in codes(signals)


# ------------------------------------------------------------ QR and audit --


class TestCertificateQr:
    def _signals(self, monkeypatch, payload: bytes):
        monkeypatch.setattr(aadhaar_qr, "extract_qr_payloads", lambda data, thorough=True: [payload])
        return orchestrator._certificate_qr_signals(b"image")

    def test_a_link_is_reported_with_its_domain_and_not_opened(self, monkeypatch):
        [link] = self._signals(monkeypatch, b"https://www.example-academy.org/verify/CA-77812")
        assert link.code == "certificate.qr.link"
        assert link.status == SignalStatus.SKIP
        assert link.evidence["domain"] == "example-academy.org"
        assert "not opened" in link.reason

    def test_a_redirect_service_is_named(self, monkeypatch):
        found = self._signals(monkeypatch, b"https://qr-codes.io/abc123")
        redirect = next(s for s in found if s.code == "certificate.qr.redirect")
        assert redirect.status == SignalStatus.WARN
        assert not redirect.blocking  # a reason to look closer, not a finding

    def test_text_that_is_not_a_link(self, monkeypatch):
        [text] = self._signals(monkeypatch, b"SOME CERTIFICATE TEXT")
        assert text.code == "certificate.qr.text"


class TestAuditMasking:
    def test_certificate_identifiers_are_masked_in_storage(self):
        stored = _redact(
            {"evidence": {"url": "https://example-academy.org/verify/CA-77812", "domain": "example-academy.org"},
             "roll": {"roll_number": "12345678"}}
        )
        assert "77812" not in stored["evidence"]["url"][:-4]
        assert stored["evidence"]["domain"] == "example-academy.org"
        assert stored["roll"]["roll_number"].endswith("5310")
        assert "2264" not in stored["roll"]["roll_number"]

    def test_the_roll_number_is_not_spelled_out_in_the_reason(self):
        signals = validate_certificate(None, text_fields("CBSE\nRoll No. 12345678\n2023"))
        roll = next(s for s in signals if s.code == "certificate.roll_number.found")
        assert "12345678" not in roll.reason and "5310" in roll.reason
