"""
An Aadhaar is accepted only once its Secure QR has been checked.

Nothing printed on an Aadhaar except the number carries a checksum, so a
photograph of the front alone cannot distinguish a genuine card from one with
an edited name, date of birth or photograph. Measured on synthetic forgeries,
60 of 122 were auto-accepted from a single image before this rule.

These pin the three properties that make the rule honest:
  - a front alone is withheld, but not scored as fraud;
  - a back submitted alongside lifts the block only if its QR was actually read
    and compared -- presence alone proves nothing;
  - a comparison that disagrees still fails, so the pair cannot launder a forgery.

Text is injected and the QR reader is stubbed, so no identity document is used.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.orchestrator import _satisfied_blocks, analyze_document, verify_case
from app.pipeline.stages.ocr import OCRResult, TextLine
from app.rules import aadhaar_qr
from app.rules.aadhaar import verhoeff_checksum
from app.rules.aadhaar_qr import AadhaarQR
from app.schemas.document import Decision, DocumentSide, DocumentType
from app.schemas.signals import SignalStatus, Stage, signal


def synthetic_number() -> str:
    """A structurally valid, made-up Aadhaar number."""
    base = "23456789012"
    check = next(d for d in "0123456789" if verhoeff_checksum(base + d) == 0)
    return base + check


NUMBER = synthetic_number()
FRONT_TEXT = (
    "GOVERNMENT OF INDIA\nAsha Verma\nDOB: 01/01/1990\nFEMALE\n"
    f"{NUMBER[:4]} {NUMBER[4:8]} {NUMBER[8:]}\nMera Aadhaar, Meri Pehchaan"
)
BACK_TEXT = (
    "Unique Identification Authority of India\n"
    "Address: 12 Station Road, Patna, Bihar 800001\n"
    "VID : 9123 4567 8901 2345\n1947\nhelp@uidai.gov.in www.uidai.gov.in"
)


def image(shade: int) -> bytes:
    """A plain card-sized image. The shade only makes the two sides distinct bytes."""
    img = np.full((520, 820, 3), shade, np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


FRONT, BACK, QR_ONLY = image(236), image(228), image(250)


class TextByImage:
    """OCR stand-in that returns different text for each side of the card."""

    name = "injected"

    def __init__(self, texts: dict[bytes, str]) -> None:
        self.texts = texts

    def read(self, image_bytes: bytes) -> OCRResult:
        lines = [
            TextLine(text=ln, confidence=1.0)
            for ln in self.texts[image_bytes].splitlines()
            if ln.strip()
        ]
        return OCRResult(lines=lines, available=True, engine="injected")


def qr_for(dob: str = "01-01-1990", last_four: str = NUMBER[-4:]) -> AadhaarQR:
    return AadhaarQR(
        version="V2",
        fields={
            "reference_id": last_four + "20240701120000000",
            "name": "Asha Verma",
            "date_of_birth": dob,
        },
    )


def stub_qr_reader(monkeypatch, on_back: AadhaarQR | None, where: bytes | None = None) -> None:
    """The back (or `where`) carries `on_back`, or an unreadable QR; the front carries none."""

    def read(image_bytes: bytes, thorough: bool = True):
        if image_bytes == (where or BACK) and on_back is not None:
            return on_back, ""
        return None, "No QR code could be read from this image."

    monkeypatch.setattr(aadhaar_qr, "read_aadhaar_qr", read)


@pytest.fixture(autouse=True)
def policy_on(monkeypatch):
    monkeypatch.setattr(settings, "aadhaar_require_qr", True)


def front_alone():
    return analyze_document(
        FRONT, "front.jpg", ocr_provider=TextByImage({FRONT: FRONT_TEXT})
    )


def codes(signals) -> set[str]:
    return {s.code for s in signals}


# ------------------------------------------------------------ single image --


class TestFrontAlone:
    def test_is_not_accepted_without_its_qr(self, monkeypatch):
        stub_qr_reader(monkeypatch, None)
        result = front_alone()
        assert result.document_type == DocumentType.AADHAAR
        assert result.side == DocumentSide.FRONT
        assert "aadhaar.qr.unchecked" in result.risk.blocking_codes
        assert result.risk.decision != Decision.ACCEPT

    def test_is_not_scored_as_fraud(self, monkeypatch):
        """Missing evidence of authenticity is not evidence of forgery."""
        stub_qr_reader(monkeypatch, None)
        unchecked = next(s for s in front_alone().signals if s.code == "aadhaar.qr.unchecked")
        assert unchecked.status == SignalStatus.WARN
        assert unchecked.severity.value == "low"
        assert front_alone().risk.band.value == "low"

    def test_tells_the_user_what_to_do_wherever_the_qr_is(self, monkeypatch):
        # Most cards print the QR on the back, some on the front: the advice
        # must not send someone whose QR is on this side off to find a back.
        stub_qr_reader(monkeypatch, None)
        unchecked = next(s for s in front_alone().signals if s.code == "aadhaar.qr.unchecked")
        assert "on the back, submit the back" in unchecked.reason
        assert "on this side, photograph it again" in unchecked.reason

    def test_the_policy_can_be_switched_off(self, monkeypatch):
        stub_qr_reader(monkeypatch, None)
        monkeypatch.setattr(settings, "aadhaar_require_qr", False)
        result = front_alone()
        assert "aadhaar.qr.unchecked" not in codes(result.signals)
        absent = next(s for s in result.signals if s.code == "aadhaar.qr.absent")
        assert absent.status == SignalStatus.SKIP

    def test_a_front_that_carries_its_own_qr_is_checked_against_it(self, monkeypatch):
        # e-Aadhaar prints and some PVC cards put the QR on the same side.
        monkeypatch.setattr(
            aadhaar_qr, "read_aadhaar_qr", lambda data, thorough=True: (qr_for(), "")
        )
        result = front_alone()
        assert "aadhaar.qr.unchecked" not in codes(result.signals)
        assert "aadhaar.qr.number_match" in codes(result.signals)


class TestBackAlone:
    def test_is_not_asked_for_the_qr_as_well(self, monkeypatch):
        """A back is already withheld as a back; one reason is enough."""
        stub_qr_reader(monkeypatch, None)
        result = analyze_document(BACK, "back.jpg", ocr_provider=TextByImage({BACK: BACK_TEXT}))
        assert result.side == DocumentSide.BACK
        assert "aadhaar.qr.unchecked" not in codes(result.signals)


# -------------------------------------------------------------- front+back --


def pair():
    return verify_case(
        [(FRONT, "front.jpg"), (BACK, "back.jpg")],
        ocr_provider=TextByImage({FRONT: FRONT_TEXT, BACK: BACK_TEXT}),
    )


class TestFrontAndBack:
    def test_a_matching_qr_on_the_back_lifts_the_block(self, monkeypatch):
        stub_qr_reader(monkeypatch, qr_for())
        result = pair()
        assert {d.side for d in result.documents} == {DocumentSide.FRONT, DocumentSide.BACK}
        assert "aadhaar.qr.number_match" in codes(result.cross_document_signals)
        assert "aadhaar.qr.unchecked" not in result.overall_risk.blocking_codes
        assert result.overall_risk.decision == Decision.ACCEPT

    def test_a_back_whose_qr_does_not_read_leaves_the_front_blocked(self, monkeypatch):
        # Both sides present, but nothing was compared: the front is exactly as
        # unauthenticated as it was on its own.
        stub_qr_reader(monkeypatch, None)
        result = pair()
        assert "aadhaar.qr.unchecked" in result.overall_risk.blocking_codes
        assert result.overall_risk.decision != Decision.ACCEPT

    def test_an_edited_date_of_birth_is_caught_by_the_pair(self, monkeypatch):
        # The front says 1990; UIDAI's signed record on the back says 1985.
        stub_qr_reader(monkeypatch, qr_for(dob="15-08-1985"))
        result = pair()
        assert codes(result.cross_document_signals) & {
            "aadhaar.qr.dob_mismatch",
            "aadhaar.qr.dob_year_mismatch",
        }
        assert result.overall_risk.decision != Decision.ACCEPT

    def test_the_case_says_what_is_holding_it(self, monkeypatch):
        stub_qr_reader(monkeypatch, None)
        risk = pair().overall_risk
        assert risk.blocking_codes.count("aadhaar.qr.unchecked") == 1
        held = risk.blocking_reasons[risk.blocking_codes.index("aadhaar.qr.unchecked")]
        assert "submit the back" in held


class TestSatisfiedBlocks:
    def test_the_qr_block_needs_a_cross_side_comparison(self):
        read_here = signal(
            code="aadhaar.qr.present",
            stage=Stage.DATABASE,
            title="Aadhaar Secure QR",
            status=SignalStatus.PASS,
            reason="read",
        )
        assert "aadhaar.qr.unchecked" not in _satisfied_blocks([], [read_here])
        read_here.evidence["cross_side"] = True
        assert "aadhaar.qr.unchecked" in _satisfied_blocks([], [read_here])


# ------------------------------------------------------ the QR photograph --


class FacelessCardProvider:
    """
    A recogniser that can embed the QR photograph but finds no face on the card.

    The shape a substituted portrait takes when the paste also defeats face
    detection -- which is what one synthetic photo swap actually did.
    """

    name = "stub"
    detection_available = True
    recognition_available = True

    class _Recogniser:
        def get_feat(self, _image):
            return np.ones(512, np.float32)

    class _App:
        def __init__(self):
            self.models = {"recognition": FacelessCardProvider._Recogniser()}

    def _get_app(self):
        return self._App()

    def analyze(self, _image_bytes):
        from app.pipeline.stages.face import FaceResult

        return FaceResult(detection_available=True, recognition_available=True)


class TestQrPhotograph:
    def test_the_check_runs_against_the_front_even_when_no_face_was_found(self, monkeypatch):
        from app.rules import aadhaar_qr_validate

        stub_qr_reader(monkeypatch, qr_for())
        compared: list[bytes] = []
        monkeypatch.setattr(
            aadhaar_qr_validate,
            "compare_qr_photo",
            lambda qr, image, provider=None: compared.append(image) or [],
        )
        pair()
        # Neither synthetic side has a face, so the front -- the side with the
        # printed fields, where the portrait belongs -- is what gets compared.
        assert compared == [FRONT]

    def test_a_card_with_no_portrait_to_compare_is_not_accepted(self, monkeypatch):
        from app.rules import aadhaar_qr as qr_module
        from app.rules.aadhaar_qr_validate import compare_qr_photo

        qr = qr_for()
        qr.photo_jp2 = b"stand-in"
        monkeypatch.setattr(qr_module, "decode_qr_photo", lambda _qr: np.zeros((60, 60, 3), np.uint8))

        [finding] = compare_qr_photo(qr, FRONT, FacelessCardProvider())
        assert finding.code == "aadhaar.qr.card_no_face"
        assert finding.is_blocking_failure
        assert finding.severity.value == "low"  # unchecked, not accused


class TestQrAlone:
    """A close-up of just the QR: no printed wording, but the QR says what it is."""

    def test_is_read_as_the_qr_side_of_an_aadhaar(self, monkeypatch):
        stub_qr_reader(monkeypatch, qr_for(), where=QR_ONLY)
        result = analyze_document(QR_ONLY, "qr.jpg", ocr_provider=TextByImage({QR_ONLY: ""}))
        assert result.document_type == DocumentType.AADHAAR
        assert result.side == DocumentSide.BACK
        assert "classify.unknown" not in codes(result.signals)
        # Alone it is withheld, as any reverse side is: nothing about the holder is in it.
        assert "classify.reverse_side" in result.risk.blocking_codes

    def test_with_the_front_it_is_compared_and_accepted(self, monkeypatch):
        stub_qr_reader(monkeypatch, qr_for(), where=QR_ONLY)
        result = verify_case(
            [(FRONT, "front.jpg"), (QR_ONLY, "qr.jpg")],
            ocr_provider=TextByImage({FRONT: FRONT_TEXT, QR_ONLY: ""}),
        )
        assert "aadhaar.qr.number_match" in codes(result.cross_document_signals)
        assert result.overall_risk.decision == Decision.ACCEPT

    def test_an_unreadable_image_stays_unknown(self, monkeypatch):
        stub_qr_reader(monkeypatch, None)
        result = analyze_document(QR_ONLY, "qr.jpg", ocr_provider=TextByImage({QR_ONLY: ""}))
        assert result.document_type == DocumentType.UNKNOWN
        assert "classify.unknown" in result.risk.blocking_codes
