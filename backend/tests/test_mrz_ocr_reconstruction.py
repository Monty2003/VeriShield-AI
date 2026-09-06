"""
Regression tests for MRZ reconstruction from real OCR output.

Every string in this file was produced by running PaddleOCR over an actual
specimen passport from Wikimedia Commons. They are kept verbatim because the
failure they encode was invisible against synthetic input: the parser scored
25/25 on hand-built MRZ strings while finding a usable MRZ in only 2 of 14
real documents.

The cause is that general-purpose OCR is not trained on the MRZ filler
character and drops runs of it, so real line 2 readings arrive at 33-35
characters instead of 44.
"""

from __future__ import annotations

from datetime import date

from app.rules.mrz import (
    MIN_RECONSTRUCTABLE_LINE2,
    PREFIX_CHECKS,
    parse_mrz,
    parse_td3,
    reconstruct_line2,
)

# --- verbatim PaddleOCR output from real specimen passports ----------------

# Norwegian specimen. OCR compressed a 14-character filler run down to 5.
NOR_L1 = "PNOROESTENBYENAASAMUNDSPECIMEN"
NOR_L2 = "CCC0022514N0R5604230M3004157<<<<<04"

# Russian diplomatic specimen. Same failure, different length.
RUS_L1 = "PDRUSSMIRNOVA<VALENTINA<<<<<<<<<<<<<<<<"
RUS_L2 = "1100000000RUS8008046F0902274<<<08"

# Dutch specimen. OCR returned the full 44 characters, so no repair is needed.
NLD_L2 = "SPEC120142NLD6503101F2403096999999990<<<<<84"


class TestReconstruction:
    def test_short_line_is_rebuilt_to_full_width(self):
        rebuilt, was_rebuilt, note = reconstruct_line2(NOR_L2)
        assert was_rebuilt is True
        assert len(rebuilt) == 44
        assert note

    def test_full_width_line_is_left_alone(self):
        rebuilt, was_rebuilt, _ = reconstruct_line2(NLD_L2)
        assert was_rebuilt is False
        assert rebuilt == NLD_L2

    def test_prefix_is_never_disturbed(self):
        """
        Positions 0-27 carry every field we actually report. Reconstruction
        must not move them, or the recovered values describe a different
        document than the one presented.
        """
        rebuilt, _, _ = reconstruct_line2(NOR_L2)
        assert rebuilt[:28] == NOR_L2[:28]

    def test_line_without_filler_is_refused(self):
        """
        A shortfall that cannot be attributed to filler means real characters
        were lost. Rebuilding would shift every field and produce confident
        nonsense, so the parser declines instead.
        """
        _, was_rebuilt, _ = reconstruct_line2("1100000000RUS8008046F0902274008")
        assert was_rebuilt is False

    def test_very_short_line_is_refused(self):
        _, was_rebuilt, _ = reconstruct_line2("CCC0022514N0R<<<")
        assert was_rebuilt is False

    def test_threshold_is_below_full_width(self):
        """Guards the constant that made real passports parseable at all."""
        assert MIN_RECONSTRUCTABLE_LINE2 < 44


class TestRealDocumentParsing:
    def test_norwegian_specimen_fields_recovered(self):
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert mrz.line2_reconstructed is True
        assert mrz.document_number == "CCC002251"
        assert mrz.date_of_birth == date(1956, 4, 23)
        assert mrz.date_of_expiry == date(2030, 4, 15)
        assert mrz.sex == "M"

    def test_russian_specimen_fields_recovered(self):
        mrz = parse_td3(RUS_L1, RUS_L2)
        assert mrz.line2_reconstructed is True
        assert mrz.document_number == "110000000"
        assert mrz.date_of_birth == date(1980, 8, 4)
        assert mrz.date_of_expiry == date(2009, 2, 27)
        assert mrz.sex == "F"

    def test_prefix_check_digits_pass_on_real_documents(self):
        """The three checks that survive reconstruction must actually verify."""
        for line1, line2 in ((NOR_L1, NOR_L2), (RUS_L1, RUS_L2)):
            mrz = parse_td3(line1, line2)
            trusted = mrz.trustworthy_checks
            assert {c.field_name for c in trusted} == PREFIX_CHECKS
            assert all(c.valid for c in trusted), f"{line2}: {mrz.failed_checks}"

    def test_mrz_discovery_finds_line2_by_shape(self):
        """
        The MRZ must be found among ordinary OCR text, and line 2 identified
        by its digit density rather than by being last -- OCR does not return
        the MRZ band in a reliable position.
        """
        noisy = [
            "ETTERNAVNETTERNAMNSOHKANAMMA/SURNAME",
            NOR_L2,
            "NASIONALITETRIIKKAVULOSVUOHTA/",
            NOR_L1,
            "UTSTEDENDEMYNDIGHETUTSKRIVANDE",
        ]
        mrz = parse_mrz(noisy)
        assert mrz is not None
        assert mrz.document_number == "CCC002251"


class TestReconstructionHonesty:
    """
    The subtle failure this guards against.

    Filler has character value 0, and so does the personal check digit it
    displaces during reconstruction. The personal-number and composite sums
    therefore come out correct no matter what the document said. Reporting
    them as passing would present an artefact of our own repair as evidence
    about the document.
    """

    def test_untrustworthy_checks_are_excluded_after_repair(self):
        mrz = parse_td3(NOR_L1, NOR_L2)
        names = {c.field_name for c in mrz.trustworthy_checks}
        assert "composite" not in names
        assert "personal_number" not in names

    def test_all_five_checks_are_still_computed_and_visible(self):
        """Excluded from scoring, but retained so a reviewer can inspect them."""
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert len(mrz.checks) == 5

    def test_unrepaired_line_trusts_every_check(self):
        mrz = parse_td3("P<NLDDEBRUIJN<WILLEKE<LISELOTTE<<<<<<<", NLD_L2)
        assert mrz.line2_reconstructed is False
        assert len(mrz.trustworthy_checks) == 5

    def test_reconstruction_note_explains_the_limitation(self):
        mrz = parse_td3(NOR_L1, NOR_L2)
        note = mrz.reconstruction_note.lower()
        assert "composite" in note
        assert "filler" in note


class TestUnprotectedFields:
    def test_nationality_misread_survives_all_check_digits(self):
        """
        TD3 composite spans positions 0-9, 13-19 and 21-42 -- it skips
        nationality at 10-12 entirely. PaddleOCR read Norway's NOR as 'N0R'
        on a real document, and no checksum can catch that. Anything
        downstream must treat nationality as unverified.
        """
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert mrz.nationality == "N0R"  # the OCR misread, undetected
        assert mrz.all_checks_valid is True  # yet every trusted check passes


class TestLine1Reliability:
    """
    Line 1 is filler-delimited end to end, so OCR ruins it completely rather
    than partially. When that happens the fields must be withheld, not
    guessed.
    """

    def test_line1_without_fillers_is_marked_unreliable(self):
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert mrz.line1_unreliable is True
        assert mrz.name_fields_reliable is False

    def test_unreliable_line1_yields_no_name(self):
        """
        A confidently wrong surname is worse than none: it reaches
        cross-document comparison and manufactures a mismatch against the same
        person's other documents.
        """
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert mrz.surname == ""
        assert mrz.given_names == ""
        assert mrz.issuing_country == ""

    def test_reviewer_is_told_why_the_name_is_missing(self):
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert any("filler" in e for e in mrz.parse_errors)

    def test_partial_filler_loss_yields_full_name_but_no_split(self):
        """
        Real OCR output: 'SMIRNOVA<VALENTINA' -- one filler where the document
        has two. The surname/given boundary is gone, but the full name is
        intact, so we return that and decline the split rather than reporting
        'SMIRNOVA VALENTINA' as a surname.
        """
        mrz = parse_td3(RUS_L1, RUS_L2)
        assert mrz.line1_unreliable is False
        assert mrz.full_name == "SMIRNOVA VALENTINA"
        assert mrz.surname == ""
        assert mrz.given_names == ""

    def test_intact_double_filler_splits_correctly(self):
        mrz = parse_td3("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", RUS_L2)
        assert mrz.surname == "ERIKSSON"
        assert mrz.given_names == "ANNA MARIA"
        assert mrz.full_name == "ANNA MARIA ERIKSSON"

    def test_filler_padding_does_not_mask_a_broken_line1(self):
        """
        Regression: the reliability check originally ran AFTER the line was
        padded to 44 characters -- and the padding uses the filler character
        itself, so a line with zero fillers was measured as having fourteen
        and passed. The check must look at the raw OCR reading.
        """
        assert NOR_L1.count("<") == 0
        mrz = parse_td3(NOR_L1, NOR_L2)
        assert len(mrz.raw_line1) == 44  # padded for offset arithmetic
        assert mrz.line1_unreliable is True  # but judged on the raw input
