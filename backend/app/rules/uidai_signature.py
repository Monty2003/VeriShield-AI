"""
Verify the RSA signature on an Aadhaar Secure QR.

Until this existed, the QR was compared against the printed card but never
authenticated. That caught a card whose printed side had been edited while the
QR was left alone -- the common case -- but not a QR fabricated to match the
edits: anyone can gzip a payload and render it as a QR. The signature is what
makes the QR evidence rather than just data, because only UIDAI's private key
can produce one that verifies.

The scheme
----------
The last 256 bytes of the decompressed payload are an RSASSA-PKCS1-v1_5
signature, SHA-256, over every byte before them, made with a UIDAI
document-signer (DS) key. The public halves live in data/certs/uidai/, with the
provenance of each recorded in the README there.

Why the QR's own date matters
-----------------------------
UIDAI rotates DS keys, and this deployment does not hold all of them -- there is
a known gap from May 2023 to Feb 2026. So "no held key verifies this" has two
very different explanations: the QR was forged, or it was signed with a key we
simply do not have. Treating every failure as forgery would call genuine cards
from the gap fakes; treating every failure as a missing key would let every
forgery through.

The reference id settles most cases. It ends with the time the QR was generated,
and it sits inside the signed bytes. If that time falls well inside a held key's
validity window, that key should have verified the QR, and its failure to is a
strong signal. If the time falls in the gap, a failure is expected and proves
nothing either way.

The gap is a weakness, not a detail: a forger who knows it can date a fabricated
QR inside it and receive only a warning. The warning says so.

Only a vetted library performs the verification. Hand-rolled PKCS#1 checks with
lax padding parsing are how real signature forgeries have worked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

if TYPE_CHECKING:
    from app.rules.aadhaar_qr import AadhaarQR

CERT_DIR = Path(__file__).resolve().parents[2] / "data" / "certs" / "uidai"

# How far inside a key's validity window a QR's date must sit before a failure
# to verify is called tampering. UIDAI does not swap signer keys on the day a
# certificate starts or ends; near the edges a genuine QR may carry either the
# outgoing key or the incoming one, and one of them may be a key we lack.
# Accusing a real card of forgery is the worst outcome available here, so the
# edges stay "cannot tell".
BOUNDARY_MARGIN = timedelta(days=60)

# The reference id's timestamp carries no zone. UIDAI is an Indian authority
# and the timestamps line up with IST download times; the choice only moves a
# boundary by hours, well inside BOUNDARY_MARGIN.
QR_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class SignerKey:
    """
    One pinned UIDAI document-signer key.

    Most come from a certificate, whose validity dates say when UIDAI meant to
    use the key. A key can also be RECOVERED from the signatures of genuine QRs
    when no certificate for its period can be found; then there is no stated
    validity at all, only the span over which the key was actually seen in use.
    That span is evidence rather than intent, so no margin is shaved off it --
    the key is known to have been signing at both ends.
    """

    name: str
    filename: str
    public_key: rsa.RSAPublicKey
    valid_from: datetime
    valid_until: datetime
    certified: bool = True
    margin: timedelta = BOUNDARY_MARGIN

    def clearly_covers(self, when: datetime) -> bool:
        """True when `when` is well inside this key's window, not near an edge."""
        return self.valid_from + self.margin <= when <= self.valid_until - self.margin

    def period(self) -> str:
        span = f"{self.valid_from:%b %Y} - {self.valid_until:%b %Y}"
        return span if self.certified else f"{span}, observed"


@dataclass(frozen=True)
class SignatureVerdict:
    """
    What verification established.

    outcome:
      verified     -- a held key validates the signature
      invalid      -- no held key does, and the QR's date says one should have
      unknown_key  -- no held key does, and the QR's date gives no reason to
                      expect one to (the gap, a window edge, or no date)
      unsigned     -- the QR carries no signature to check
      no_keys      -- nothing to check it against: the certificates are missing
    """

    outcome: str
    key: SignerKey | None = None
    expected_key: SignerKey | None = None
    generated_at: datetime | None = None


def load_signer_keys(directory: Path = CERT_DIR) -> tuple[SignerKey, ...]:
    """
    Every RSA certificate in `directory`, plus any recovered keys, newest first.

    A certificate that will not parse is skipped rather than fatal: one bad file
    should not stop the others from verifying. Expired certificates are kept on
    purpose -- a card signed in 2021 is still valid and still carries a 2021
    signature.
    """
    keys: list[SignerKey] = []
    if not directory.is_dir():
        return ()
    for path in sorted(directory.glob("*.pem")):
        try:
            cert = x509.load_pem_x509_certificate(path.read_bytes())
        except ValueError:
            continue
        public_key = cert.public_key()
        if not isinstance(public_key, rsa.RSAPublicKey):
            continue
        names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        keys.append(
            SignerKey(
                name=str(names[0].value) if names else path.stem,
                filename=path.name,
                public_key=public_key,
                valid_from=cert.not_valid_before_utc,
                valid_until=cert.not_valid_after_utc,
            )
        )
    keys.extend(_recovered_keys(directory))
    keys.sort(key=lambda k: k.valid_from, reverse=True)
    return tuple(keys)


def _recovered_keys(directory: Path) -> list[SignerKey]:
    """
    Keys recovered from QR signatures, stored as JSON beside the certificates.

    Each record carries its modulus, the span of dates over which genuine QRs
    were seen signed with it, and a SHA-256 of the modulus. A record whose hash
    does not match is ignored: an accidental edit to a key that nothing
    certifies should fail loudly by disappearing, not start verifying the
    wrong things.
    """
    import hashlib
    import json

    keys: list[SignerKey] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            modulus = int(record["modulus_hex"], 16)
            expected = record.get("modulus_sha256")
            actual = hashlib.sha256(modulus.to_bytes(256, "big")).hexdigest()
            if expected is not None and expected != actual:
                continue
            public_key = rsa.RSAPublicNumbers(
                int(record.get("exponent", 65537)), modulus
            ).public_key()
            start = datetime.fromisoformat(record["observed_from"])
            end = datetime.fromisoformat(record["observed_until"])
        except (KeyError, ValueError, OverflowError, TypeError):
            continue
        keys.append(
            SignerKey(
                name=str(record.get("name", path.stem)),
                filename=path.name,
                public_key=public_key,
                valid_from=start.replace(tzinfo=QR_TIMEZONE),
                # The whole final day, not its first second.
                valid_until=(end + timedelta(days=1, seconds=-1)).replace(
                    tzinfo=QR_TIMEZONE
                ),
                certified=False,
                margin=timedelta(0),
            )
        )
    return keys


@lru_cache(maxsize=1)
def pinned_keys() -> tuple[SignerKey, ...]:
    """The deployment's keys, read once per process."""
    return load_signer_keys()


def qr_generated_at(qr: AadhaarQR) -> datetime | None:
    """
    When the QR was generated, from its reference id.

    The reference id is the last four digits of the Aadhaar number followed by
    YYYYMMDDHHMMSSsss. Only the timestamp is read; the digits are never
    returned.
    """
    reference = str(qr.fields.get("reference_id", ""))
    stamp = reference[4:18]
    if len(stamp) != 14 or not stamp.isdigit():
        return None
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=QR_TIMEZONE)
    except ValueError:
        return None


def verify_qr_signature(
    qr: AadhaarQR, keys: tuple[SignerKey, ...] | None = None
) -> SignatureVerdict:
    """Check a parsed Secure QR's signature against the pinned UIDAI keys."""
    keys = pinned_keys() if keys is None else keys
    when = qr_generated_at(qr)

    if not qr.signature or not qr.signed_payload:
        return SignatureVerdict("unsigned", generated_at=when)
    if not keys:
        return SignatureVerdict("no_keys", generated_at=when)

    for key in keys:
        try:
            key.public_key.verify(
                qr.signature, qr.signed_payload, padding.PKCS1v15(), hashes.SHA256()
            )
        except InvalidSignature:
            continue
        return SignatureVerdict("verified", key=key, generated_at=when)

    if when is not None:
        expected = next((k for k in keys if k.clearly_covers(when)), None)
        if expected is not None:
            return SignatureVerdict("invalid", expected_key=expected, generated_at=when)

    return SignatureVerdict("unknown_key", generated_at=when)


def coverage_summary(keys: tuple[SignerKey, ...] | None = None) -> str:
    """One line for /health: which periods can be verified."""
    keys = pinned_keys() if keys is None else keys
    if not keys:
        return "unavailable: no UIDAI signer certificates are installed"
    periods = ", ".join(k.period() for k in sorted(keys, key=lambda k: k.valid_from))
    return f"{len(keys)} UIDAI signer keys ({periods})"
