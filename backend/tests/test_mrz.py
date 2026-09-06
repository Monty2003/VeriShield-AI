"""
MRZ parsing and check-digit tests.

The ICAO specimen is the anchor: if these pass, the check-digit arithmetic is
correct against the published standard, and every tampering test built on top
of it means something.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.rules.mrz import (
    char_value,
    check_digit,
    parse_mrz,
    parse_mrz_date,
    parse_mrz_name,
    parse_td3,
    verify_check_digit,
)

# ICAO 9303 published specimen: Anna Maria Eriksson, Utopia.
SPEC_L1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
SPEC_L2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


class TestCharValue:
    def test_digits_are_themselves(self):
        assert [char_value(str(d)) for d in range(10)] == list(range(10))

    def test_letters_start_at_ten(self):
        assert char_value("A") == 10
        assert char_value("Z") == 35

    def test_filler_is_zero(self):
        assert char_value("<") == 0

    def test_invalid_character_rejected(self):
        with pytest.raises(ValueError):
            char_value("!")


class TestCheckDigit:
    def test_icao_specimen_document_number(self):
        assert check_digit("L898902C3") == 6

    def test_icao_specimen_dob(self):
        assert check_digit("740812") == 2

    def test_icao_specimen_expiry(self):
        assert check_digit("120415") == 9

    def test_filler_digit_treated_as_zero(self):
        assert verify_check_digit("<" * 14, "<") is True

    def test_non_numeric_digit_is_invalid(self):
        assert verify_check_digit("740812", "X") is False


class TestDateParsing:
    def test_expiry_always_this_century(self):
        assert parse_mrz_date("330411", is_expiry=True) == date(2033, 4, 11)

    def test_birth_year_ahead_of_now_is_last_century(self):
        # 99 cannot be 2099 for a living passport holder.
        assert parse_mrz_date("990412").year == 1999

    def test_impossible_date_returns_none(self):
        assert parse_mrz_date("740230") is None  # 30 February

    def test_non_numeric_returns_none(self):
        assert parse_mrz_date("7408AB") is None


class TestNameParsing:
    def test_surname_and_given_names_split(self):
        surname, given, full = parse_mrz_name("ERIKSSON<<ANNA<MARIA<<<<<<<<")
        assert surname == "ERIKSSON"
        assert given == "ANNA MARIA"
        assert full == "ERIKSSON ANNA MARIA"

    def test_surname_only(self):
        """Trailing filler is stripped, so no double separator remains."""
        surname, given, full = parse_mrz_name("SUMAN<<<<<<<<<")
        assert full == "SUMAN"
        assert surname == ""
        assert given == ""

    def test_single_filler_between_names_declines_the_split(self):
        """
        OCR routinely collapses the '<<' surname separator to '<'. Splitting
        on what survives would report 'SMIRNOVA VALENTINA' as a surname, so
        the parser returns the full name and declines the split instead.
        """
        surname, given, full = parse_mrz_name("SMIRNOVA<VALENTINA<<<<<<")
        assert full == "SMIRNOVA VALENTINA"
        assert surname == ""
        assert given == ""


class TestTD3Parsing:
    def test_icao_specimen_parses_completely(self):
        mrz = parse_td3(SPEC_L1, SPEC_L2)
        assert mrz.surname == "ERIKSSON"
        assert mrz.given_names == "ANNA MARIA"
        assert mrz.document_number == "L898902C3"
        assert mrz.nationality == "UTO"
        assert mrz.date_of_birth == date(1974, 8, 12)
        assert mrz.date_of_expiry == date(2012, 4, 15)
        assert mrz.sex == "F"
        assert mrz.parse_errors == []

    def test_icao_specimen_all_check_digits_valid(self):
        assert parse_td3(SPEC_L1, SPEC_L2).all_checks_valid is True

    def test_short_lines_are_padded_not_crashed(self):
        mrz = parse_td3("P<IND", "Z3456789")
        assert len(mrz.raw_line1) == 44
        assert len(mrz.raw_line2) == 44

    def test_non_passport_document_code_flagged(self):
        mrz = parse_td3("I<UTOERIKSSON<<ANNA<<<<<<<<<<<<<<<<<<<<<<<<<", SPEC_L2)
        assert any("expected P" in e for e in mrz.parse_errors)


class TestTamperDetection:
    """
    The core security property: editing a data field without recomputing its
    check digits is detectable, and breaks TWO digits at once because the
    composite covers every field.
    """

    def test_edited_dob_breaks_own_and_composite_digits(self):
        tampered = SPEC_L2[:13] + "840812" + SPEC_L2[19:]
        failed = {c.field_name for c in parse_td3(SPEC_L1, tampered).failed_checks}
        assert failed == {"date_of_birth", "composite"}

    def test_edited_expiry_breaks_own_and_composite_digits(self):
        tampered = SPEC_L2[:21] + "301231" + SPEC_L2[27:]
        failed = {c.field_name for c in parse_td3(SPEC_L1, tampered).failed_checks}
        assert failed == {"date_of_expiry", "composite"}

    def test_edited_document_number_breaks_own_and_composite_digits(self):
        tampered = "Z999999X9" + SPEC_L2[9:]
        failed = {c.field_name for c in parse_td3(SPEC_L1, tampered).failed_checks}
        assert failed == {"document_number", "composite"}

    def test_recomputing_field_digit_alone_still_fails_composite(self):
        """
        A forger who fixes the obvious check digit is still caught.

        This is the property that makes MRZ validation worth having: the
        composite digit covers the other check digits too, so a partial fix
        does not produce a consistent document.
        """
        new_dob = "840812"
        tampered = (
            SPEC_L2[:13] + new_dob + str(check_digit(new_dob)) + SPEC_L2[20:]
        )
        result = parse_td3(SPEC_L1, tampered)
        failed = {c.field_name for c in result.failed_checks}
        assert failed == {"composite"}
        assert result.all_checks_valid is False


class TestMRZDiscovery:
    def test_returns_none_when_no_plausible_lines(self):
        assert parse_mrz(["REPUBLIC OF INDIA", "PASSPORT"]) is None

    def test_finds_trailing_line_pair(self):
        mrz = parse_mrz(["NOISE", "REPUBLIC OF INDIA", SPEC_L1, SPEC_L2])
        assert mrz is not None
        assert mrz.surname == "ERIKSSON"
