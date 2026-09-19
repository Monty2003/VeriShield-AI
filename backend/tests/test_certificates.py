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
    CERT = "\n".join(
        [
            "CODE ALPHA",
            "CERTIFICATE OF COMPLETION",
            "This Certificate Is Proudly Presented To",
            "Student ID:EX/D1/0042",
            "1st December 2024 to 30th December 2024",
        ]
    )

    def _signals(self, monkeypatch, payload: bytes, text: str = CERT):
        monkeypatch.setattr(aadhaar_qr, "extract_qr_payloads", lambda data, thorough=True: [payload])
        return {s.code: s for s in orchestrator._certificate_qr_signals(b"image", text)}

    def test_a_link_is_reported_with_what_the_page_should_show(self, monkeypatch):
        link = self._signals(monkeypatch, b"https://www.codealpha.tech/verify")["certificate.qr.link"]
        assert link.status == SignalStatus.SKIP
        assert link.evidence["domain"] == "codealpha.tech"
        assert "not opened here" in link.reason
        assert "ending 0042" in link.reason  # the checklist names the printed ID

    def test_a_link_to_the_issuer_the_certificate_names(self, monkeypatch):
        found = self._signals(monkeypatch, b"https://www.codealpha.tech/verify")
        assert found["certificate.qr.issuer_site"].status == SignalStatus.PASS
        assert "certificate.qr.unrelated_site" not in found

    def test_a_link_to_a_site_the_certificate_never_names(self, monkeypatch):
        found = self._signals(monkeypatch, b"https://cheap-certs.example.net/c/1")
        unrelated = found["certificate.qr.unrelated_site"]
        assert unrelated.status == SignalStatus.WARN
        assert not unrelated.blocking  # a reason to look closer, not a finding

    def test_a_short_site_name_must_stand_as_a_word(self, monkeypatch):
        # certs.ine.com: "INE" is inside "ONLINE"; that is not the issuer named.
        found = self._signals(monkeypatch, b"https://certs.ine.com/x", "COMPLETED ONLINE COURSE")
        assert "certificate.qr.unrelated_site" in found
        found = self._signals(monkeypatch, b"https://certs.ine.com/x", "INE SECURITY\nCERTIFICATE")
        assert "certificate.qr.issuer_site" in found

    def test_a_link_that_carries_the_printed_id(self, monkeypatch):
        found = self._signals(monkeypatch, b"https://codealpha.tech/verify?id=EX-D1-0042")
        assert found["certificate.qr.carries_id"].status == SignalStatus.PASS

    def test_a_redirect_service_is_named_and_not_credited_as_the_issuer(self, monkeypatch):
        found = self._signals(monkeypatch, b"https://qr-codes.io/abc123")
        assert found["certificate.qr.redirect"].status == SignalStatus.WARN
        assert "certificate.qr.issuer_site" not in found
        assert "certificate.qr.unrelated_site" not in found

    def test_text_that_is_not_a_link(self, monkeypatch):
        assert "certificate.qr.text" in self._signals(monkeypatch, b"SOME CERTIFICATE TEXT")


class TestPrintedIdsAndDates:
    def test_ids_under_every_usual_label(self):
        from app.rules.certificate import printed_ids

        assert printed_ids("Student ID:EX/D1/0042") == ["EX/D1/0042"]
        assert printed_ids("Reg No. INST/2026/0077") == ["INST/2026/0077"]
        assert printed_ids("Credential ID ABC-12345") == ["ABC-12345"]
        assert printed_ids("Student ID: pending") == []  # an ID has a digit

    def test_the_certificate_says_which_id_to_look_up(self):
        text = INTERNSHIP + "\nStudent ID:EX/D1/0042"
        reference = next(
            s for s in validate_certificate(None, text_fields(text))
            if s.code == "certificate.reference.found"
        )
        assert "ending 0042" in reference.reason

    def _dates(self, text: str):
        from datetime import date

        from app.rules.certificate import _date_signals

        return {s.code: s for s in _date_signals(text, today=date(2026, 9, 20))}

    def test_a_genuine_period_and_issue_date(self):
        # As OCR read a genuine certificate: "to" joined to the day.
        found = self._dates("1st December 2024 to30th December 2024\n2nd January 2025\nDate of Issue")
        assert "certificate.dates.consistent" in found

    def test_issued_before_the_period_ended(self):
        found = self._dates("1st December 2024 to 30th December 2024\nDate of Issue 2nd December 2024")
        bad = found["certificate.dates.inconsistent"]
        assert bad.is_blocking_failure and "before the period" in bad.reason

    def test_a_period_that_ends_before_it_begins(self):
        assert "certificate.dates.inconsistent" in self._dates("from 30 December 2024 to 1 December 2024")

    def test_a_date_not_yet_reached(self):
        assert "certificate.dates.inconsistent" in self._dates("Date of Issue: 12/11/2026")

    def test_a_validity_date_may_be_in_the_future(self):
        found = self._dates("Issued on 01/01/2025. Valid up to 31/12/2027")
        assert "certificate.dates.consistent" in found


class TestAuditMasking:
    def test_certificate_identifiers_are_masked_in_storage(self):
        stored = _redact(
            {"evidence": {"url": "https://example-academy.org/verify/CA-77812", "domain": "example-academy.org"},
             "roll": {"roll_number": "12345678"}}
        )
        assert "77812" not in stored["evidence"]["url"][:-4]
        assert stored["evidence"]["domain"] == "example-academy.org"
        assert stored["roll"]["roll_number"].endswith("5678")
        assert "1234" not in stored["roll"]["roll_number"]

    def test_the_roll_number_is_not_spelled_out_in_the_reason(self):
        signals = validate_certificate(None, text_fields("CBSE\nRoll No. 12345678\n2023"))
        roll = next(s for s in signals if s.code == "certificate.roll_number.found")
        assert "12345678" not in roll.reason and "5678" in roll.reason


# ---------------------------------------------------- the table, as a table --

from app.pipeline.stages.ocr import OCRResult, TextLine  # noqa: E402
from app.rules.certificate import find_number_word_phrases, words_to_number  # noqa: E402
from app.schemas.signals import Region  # noqa: E402


def box(text: str, x: int, y: int, h: int = 55) -> TextLine:
    return TextLine(text=text, confidence=0.95, region=Region(x=x, y=y, width=120, height=h))


def marksheet(physics_total: str = "067") -> OCRResult:
    """
    Three rows laid out exactly as OCR boxed a genuine CBSE marksheet: the
    words column sits a few pixels above the digits, and a narrow cell's words
    come out joined. Subjects are real CBSE subjects; the marks are made up.
    """
    # In the order the engine returned them -- which is the defect: PHYSICS's
    # words come straight after ENGLISH's figures, and before PHYSICS's own.
    lines = [
        box("CENTRAL BOARD OF SECONDARY EDUCATION", 640, 640),
        box("MARKS STATEMENT CUM CERTIFICATE", 880, 820),
        box("ENGLISH CORE", 470, 2232),
        box("044", 1430, 2231), box("020", 1643, 2227), box("064", 1826, 2227),
        box("SIXTY FOUR", 2026, 2227), box("C2", 2543, 2223), box("301", 291, 2236),
        box("SIXTY SEVEN", 2026, 2291), box("B2", 2531, 2283), box("042", 283, 2304),
        box("040", 1430, 2299), box("027", 1647, 2299), box(physics_total, 1830, 2295),
        box("PHYSICS", 470, 2300),
        box("B2", 2531, 2351), box("041", 1430, 2367), box("026", 1647, 2367),
        box("067", 1830, 2363), box("SIXTYSEVEN", 2030, 2363), box("043", 291, 2376),
        box("CHEMISTRY", 470, 2370),
    ]
    return OCRResult(lines=lines, available=True, engine="test")


def check(text: str):
    return {s.code: s for s in validate_certificate(None, text_fields(text))}


class TestTableRows:
    def test_the_engine_order_pairs_a_total_with_the_row_above(self):
        """The defect, as it was: SIXTY SEVEN compared with ENGLISH's figures."""
        assert "certificate.totals.mismatch" in check(marksheet().full_text)

    def test_rebuilt_rows_pair_every_total_with_its_own_figures(self):
        found = check(marksheet().rows_text())
        assert "certificate.totals.mismatch" not in found
        assert len(found["certificate.totals.match"].evidence["verified"]) == 3

    def test_each_rebuilt_row_reads_left_to_right(self):
        rows = marksheet().rows_text().splitlines()
        assert "042 PHYSICS 040 027 067 SIXTY SEVEN B2" in rows

    def test_a_raised_mark_is_still_caught_and_now_holds_the_document(self):
        found = check(marksheet(physics_total="087").rows_text())
        mismatch = found["certificate.totals.mismatch"]
        assert mismatch.is_blocking_failure  # was scored 15 and accepted
        assert mismatch.confidence < 0.8  # OCR can misread a digit: escalate, not conclude

    def test_without_boxes_the_engine_order_is_kept(self):
        plain = OCRResult(lines=[TextLine(text="A", confidence=1.0), TextLine(text="B", confidence=1.0)])
        assert plain.rows_text() == "A\nB"


class TestJoinedNumberWords:
    def test_ocr_joined_words_are_read(self):
        assert words_to_number("SEVENTYSIX") == 76
        assert dict(find_number_word_phrases("TOTAL FIFTYTWO C1")) == {"FIFTYTWO": 52}

    def test_ordinary_words_are_not_read_as_numbers(self):
        assert find_number_word_phrases("TENANT OFTEN ONETIME PHONE") == []


class TestPipelineUsesTheRows:
    def test_the_rulebook_reads_rows_and_extraction_is_unchanged(self, monkeypatch):
        import cv2
        import numpy as np

        from app.pipeline.orchestrator import analyze_document

        class Boxed:
            name = "boxed"

            def read(self, _image):
                return marksheet()

        image = cv2.imencode(".jpg", np.full((3000, 2700, 3), 240, np.uint8))[1].tobytes()
        result = analyze_document(image, "marksheet.jpg", ocr_provider=Boxed())
        assert result.document_type == DocumentType.CERTIFICATE
        codes_found = {s.code for s in result.signals}
        assert "certificate.totals.match" in codes_found
        assert "certificate.totals.mismatch" not in codes_found
