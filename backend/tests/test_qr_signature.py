"""
Aadhaar Secure QR signature verification.

Synthetic QRs are built and signed here with throwaway keys, so the suite needs
no real identity document. The real-QR checks at the bottom run only where such
files exist locally (they are gitignored) and assert on outcomes alone --
nothing from a QR is ever printed or compared by value.

The cases that matter most are the ones a naive verifier gets wrong in the
dangerous direction:
  * a QR signed with a forger's OWN key -- a perfectly well-formed signature,
    just not UIDAI's -- must not pass;
  * a QR from a period whose key is not held must NOT be called a forgery,
    because genuine cards from then look exactly like that.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.risk.engine import assess_document
from app.rules import uidai_signature
from app.rules.aadhaar_qr import (
    _FIELDS,
    DELIMITER,
    JP2_MAGIC,
    parse_secure_qr,
    read_aadhaar_qr,
)
from app.rules.aadhaar_qr_validate import validate_against_qr
from app.rules.uidai_signature import (
    SignerKey,
    load_signer_keys,
    qr_generated_at,
    verify_qr_signature,
)
from app.schemas.document import ExtractedFields
from app.schemas.signals import Stage

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)
WINDOW_UNTIL = datetime(2023, 1, 1, tzinfo=timezone.utc)
INSIDE = datetime(2021, 7, 25, 12, 0, tzinfo=IST)
IN_THE_GAP = datetime(2024, 7, 17, 12, 0, tzinfo=IST)
NEAR_THE_EDGE = WINDOW_FROM.astimezone(IST) + timedelta(days=10)


@pytest.fixture(scope="module")
def uidai_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def forger_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def held(uidai_key):
    return (
        SignerKey(
            name="TEST DS",
            filename="test.pem",
            public_key=uidai_key.public_key(),
            valid_from=WINDOW_FROM,
            valid_until=WINDOW_UNTIL,
        ),
    )


def build_qr(signer, when: datetime, *, name: str = "Test Holder", sign: bool = True):
    """A V5-layout Secure QR, signed by `signer`, as parse_secure_qr sees it."""
    values = {
        "email_mobile_flag": "0",
        # Four placeholder digits, then the generation timestamp. Not a real number.
        "reference_id": "0000" + when.strftime("%Y%m%d%H%M%S") + "000",
        "name": name,
        "date_of_birth": "01-01-1990",
        "gender": "M",
        "pincode": "560001",
        "state": "Karnataka",
    }
    head = DELIMITER.join(
        [b"V5"] + [values.get(field, "x").encode() for field in _FIELDS]
    )
    body = head + DELIMITER + JP2_MAGIC + b"not-really-a-photo"
    if sign:
        body += signer.sign(body, padding.PKCS1v15(), hashes.SHA256())
    return parse_secure_qr(str(int.from_bytes(gzip.compress(body), "big")))


def edit_after_signing(qr, **changes):
    """Rewrite fields of an already-signed QR, keeping its original signature."""
    for field, value in changes.items():
        old = qr.fields[field].encode()
        qr.signed_payload = qr.signed_payload.replace(old, value.encode(), 1)
        qr.fields[field] = value
    return qr


# --------------------------------------------------------------- verifier --


class TestVerifier:
    def test_a_genuine_qr_verifies(self, uidai_key, held):
        verdict = verify_qr_signature(build_qr(uidai_key, INSIDE), held)
        assert verdict.outcome == "verified"
        assert verdict.key.name == "TEST DS"

    def test_an_edited_qr_inside_a_held_window_is_invalid(self, uidai_key, held):
        qr = edit_after_signing(build_qr(uidai_key, INSIDE), name="Someone Else")
        verdict = verify_qr_signature(qr, held)
        assert verdict.outcome == "invalid"
        assert verdict.expected_key.name == "TEST DS"

    def test_a_qr_signed_with_a_forgers_own_key_is_invalid(self, forger_key, held):
        # The real attack: a well-formed signature that simply is not UIDAI's.
        verdict = verify_qr_signature(build_qr(forger_key, INSIDE), held)
        assert verdict.outcome == "invalid"

    def test_a_failure_dated_in_the_gap_is_not_called_forgery(self, forger_key, held):
        verdict = verify_qr_signature(build_qr(forger_key, IN_THE_GAP), held)
        assert verdict.outcome == "unknown_key"

    def test_near_a_key_changeover_it_does_not_accuse(self, forger_key, held):
        verdict = verify_qr_signature(build_qr(forger_key, NEAR_THE_EDGE), held)
        assert verdict.outcome == "unknown_key"

    def test_with_no_keys_it_says_so_instead_of_passing(self, uidai_key):
        verdict = verify_qr_signature(build_qr(uidai_key, INSIDE), ())
        assert verdict.outcome == "no_keys"

    def test_an_unsigned_qr_is_unsigned(self, uidai_key, held):
        verdict = verify_qr_signature(build_qr(uidai_key, INSIDE, sign=False), held)
        assert verdict.outcome == "unsigned"

    def test_the_generation_time_is_read_from_the_reference_id(self, uidai_key):
        qr = build_qr(uidai_key, INSIDE)
        assert qr_generated_at(qr) == INSIDE.replace(microsecond=0)


# ---------------------------------------------------------------- signals --


@pytest.fixture
def pinned_to(monkeypatch):
    def pin(keys):
        monkeypatch.setattr(uidai_signature, "pinned_keys", lambda: keys)

    return pin


def signature_signal(qr):
    return next(
        s
        for s in validate_against_qr(qr, ExtractedFields())
        if s.code.startswith("aadhaar.qr.signature")
    )


class TestSignals:
    def test_verified_passes_and_names_the_key(self, uidai_key, held, pinned_to):
        pinned_to(held)
        s = signature_signal(build_qr(uidai_key, INSIDE))
        assert (s.code, s.status.value) == ("aadhaar.qr.signature.verified", "pass")
        assert s.evidence["signer"] == "TEST DS"

    def test_invalid_blocks_acceptance(self, forger_key, held, pinned_to):
        pinned_to(held)
        s = signature_signal(build_qr(forger_key, INSIDE))
        assert (s.code, s.status.value, s.blocking) == (
            "aadhaar.qr.signature.invalid",
            "fail",
            True,
        )
        risk = assess_document([s], expected_stages=(Stage.DATABASE,))
        assert risk.decision.value != "accept"

    def test_unknown_key_warns_without_blocking(self, forger_key, held, pinned_to):
        # Blocking here would send every genuine card from the gap to review.
        pinned_to(held)
        s = signature_signal(build_qr(forger_key, IN_THE_GAP))
        assert (s.code, s.status.value, s.blocking) == (
            "aadhaar.qr.signature.unknown_key",
            "warn",
            False,
        )

    def test_missing_certificates_are_an_error_not_a_pass(self, uidai_key, pinned_to):
        pinned_to(())
        s = signature_signal(build_qr(uidai_key, INSIDE))
        assert (s.code, s.status.value) == ("aadhaar.qr.signature.unavailable", "error")

    def test_evidence_never_carries_the_reference_id(self, uidai_key, held, pinned_to):
        pinned_to(held)
        s = signature_signal(build_qr(uidai_key, INSIDE))
        assert "0000" + INSIDE.strftime("%Y%m%d") not in str(s.evidence)


# ------------------------------------------------------- pinned UIDAI keys --


class TestPinnedKeys:
    def test_the_uidai_keys_load_newest_first(self):
        keys = load_signer_keys()
        assert all(k.public_key.key_size == 2048 for k in keys)
        assert [(k.filename, k.certified) for k in keys] == [
            ("ds_uidai_06_2026-2029.pem", True),
            ("recovered_uidai_2024-2025.json", False),
            ("ds_uidai_01_2020-2023.pem", True),
            ("ds_uidai_4_2017-2020.pem", True),
        ]

    def test_only_the_observed_span_of_the_old_gap_is_claimed(self):
        # The recovered key closes Jul 2024 - Jul 2025. Either side of that,
        # until the next certificate, nothing should claim to know which key
        # UIDAI was using -- a failure there must stay a warning.
        keys = load_signer_keys()
        still_open = [
            datetime(2023, 10, 1, tzinfo=IST),
            datetime(2025, 11, 1, tzinfo=IST),
        ]
        for when in still_open:
            assert not any(k.clearly_covers(when) for k in keys), when
        covering = [k for k in keys if k.clearly_covers(datetime(2024, 12, 1, tzinfo=IST))]
        assert [k.filename for k in covering] == ["recovered_uidai_2024-2025.json"]


# ------------------------------------------------------- recovered keys --


def recovered_record(key, start="2024-07-01", end="2025-07-31"):
    """A recovered-key record, as it would sit beside the certificates."""
    n = key.public_key().public_numbers().n
    return {
        "name": "TEST recovered",
        "exponent": 65537,
        "modulus_hex": format(n, "x"),
        "modulus_sha256": hashlib.sha256(n.to_bytes(256, "big")).hexdigest(),
        "observed_from": start,
        "observed_until": end,
    }


class TestRecoveredKeys:
    """
    A key recovered from QR signatures has no certificate, only a span of dates
    over which it was seen signing. These pin how that differs from a
    certified key -- in what it claims, and in how the reviewer is told.
    """

    def test_it_loads_as_uncertified_with_no_margin(self, tmp_path, uidai_key):
        (tmp_path / "r.json").write_text(json.dumps(recovered_record(uidai_key)))
        (key,) = load_signer_keys(tmp_path)
        assert key.certified is False
        assert key.margin == timedelta(0)
        assert "observed" in key.period()

    def test_an_edited_modulus_is_refused(self, tmp_path, uidai_key):
        record = recovered_record(uidai_key)
        record["modulus_sha256"] = "0" * 64
        (tmp_path / "r.json").write_text(json.dumps(record))
        assert load_signer_keys(tmp_path) == ()

    def test_the_observed_span_counts_right_up_to_its_ends(self, tmp_path, uidai_key):
        # Signatures were SEEN at both ends, so unlike a certificate window
        # there is no edge uncertainty to shave off.
        (tmp_path / "r.json").write_text(json.dumps(recovered_record(uidai_key)))
        (key,) = load_signer_keys(tmp_path)
        assert key.clearly_covers(datetime(2024, 7, 1, 0, 0, tzinfo=IST))
        assert key.clearly_covers(datetime(2025, 7, 31, 23, 0, tzinfo=IST))
        assert not key.clearly_covers(datetime(2025, 8, 2, tzinfo=IST))

    def test_verifying_with_it_tells_the_reviewer(self, tmp_path, uidai_key, pinned_to):
        (tmp_path / "r.json").write_text(json.dumps(recovered_record(uidai_key)))
        pinned_to(load_signer_keys(tmp_path))
        s = signature_signal(build_qr(uidai_key, datetime(2024, 12, 1, tzinfo=IST)))
        assert s.code == "aadhaar.qr.signature.verified"
        assert s.evidence["certified"] is False
        assert s.confidence == 0.9
        assert "recovered" in s.reason

    def test_a_forgery_dated_inside_the_span_is_invalid(
        self, tmp_path, uidai_key, forger_key, pinned_to
    ):
        (tmp_path / "r.json").write_text(json.dumps(recovered_record(uidai_key)))
        pinned_to(load_signer_keys(tmp_path))
        s = signature_signal(build_qr(forger_key, datetime(2024, 12, 1, tzinfo=IST)))
        assert (s.code, s.blocking) == ("aadhaar.qr.signature.invalid", True)
        assert "other genuine QRs" in s.reason


# --------------------------------------------- real QRs (local files only) --

RAW = Path(__file__).resolve().parents[1] / "data" / "datasets" / "raw" / "aadhaar"
OWN_QR = RAW / "my_eaadhaar_qr.png"


@pytest.mark.skipif(not OWN_QR.exists(), reason="no local e-Aadhaar QR (gitignored)")
def test_a_real_e_aadhaar_qr_verifies_with_the_current_uidai_key():
    qr, _ = read_aadhaar_qr(OWN_QR.read_bytes())
    verdict = verify_qr_signature(qr, load_signer_keys())
    assert verdict.outcome == "verified"
    assert verdict.key.filename == "ds_uidai_06_2026-2029.pem"


def _local_signed_qrs():
    for path in sorted(RAW.iterdir()) if RAW.is_dir() else ():
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".heic", ""}:
            continue
        try:
            qr, _ = read_aadhaar_qr(path.read_bytes())
        except Exception:  # noqa: BLE001 -- unreadable images are not the subject
            continue
        if qr is not None and qr.signature:
            yield path, qr


@pytest.mark.skipif(not RAW.is_dir(), reason="no local raw Aadhaar images")
def test_real_qrs_from_the_recovered_span_verify_with_the_recovered_key():
    """The committed recovered key must keep verifying the QRs it came from."""
    keys = load_signer_keys()
    recovered = next(k for k in keys if not k.certified)
    in_span = [
        (path, qr)
        for path, qr in _local_signed_qrs()
        if (when := qr_generated_at(qr)) and recovered.clearly_covers(when)
    ]
    if not in_span:
        pytest.skip("no local QR from the recovered key's span")
    for path, qr in in_span:
        verdict = verify_qr_signature(qr, keys)
        assert (verdict.outcome, verdict.key.filename) == (
            "verified",
            recovered.filename,
        ), path.name


@pytest.mark.skipif(not RAW.is_dir(), reason="no local raw Aadhaar images")
def test_no_real_qr_is_ever_called_tampered():
    """
    raw/aadhaar holds genuine cards. If one is reported as tampered, a pinned
    certificate is wrong or parsing has regressed -- either would accuse real
    people of forgery.
    """
    keys = load_signer_keys()
    checked = 0
    for path in sorted(RAW.iterdir()):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".heic", ""}:
            continue
        try:
            qr, _ = read_aadhaar_qr(path.read_bytes())
        except Exception:  # noqa: BLE001 -- unreadable images are not the subject
            continue
        if qr is None or not qr.signature:
            continue
        checked += 1
        assert verify_qr_signature(qr, keys).outcome != "invalid", path.name
    if checked == 0:
        pytest.skip("no readable signed QR among the local images")
