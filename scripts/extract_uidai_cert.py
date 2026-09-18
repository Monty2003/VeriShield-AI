"""
Extract UIDAI's current signer certificate from an e-Aadhaar PDF.

UIDAI signs the e-Aadhaar PDF with the same document-signer key it uses for the
Secure QR, and a PDF signature embeds the full certificate chain. ISO 32000
exempts the signature's /Contents from encryption, so the certificate can be
read without the PDF password -- and without opening any of the holder's data,
which stays encrypted and is never touched here.

This is how the project keeps up with UIDAI's key rotation: download any fresh
e-Aadhaar, run this, and the current key is in.

A certificate is only written when it has verified a real QR. Pinning a key
that has never been seen to verify anything would turn every genuine card into
an apparent forgery the day it went wrong.

Usage:
    python scripts/extract_uidai_cert.py EAadhaar.pdf
    python scripts/extract_uidai_cert.py EAadhaar.pdf --qr qr.png --write
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs7  # noqa: E402

CERT_DIR = BACKEND / "data" / "certs" / "uidai"


def _der_length(blob: bytes) -> int:
    """Total length of the DER SEQUENCE at the start of blob, header included."""
    if not blob or blob[0] != 0x30:
        raise ValueError("signature is not a DER SEQUENCE")
    first = blob[1]
    if first < 0x80:
        return 2 + first
    count = first & 0x7F
    return 2 + count + int.from_bytes(blob[2 : 2 + count], "big")


def signature_certificates(pdf: bytes) -> list[x509.Certificate]:
    """Every certificate carried by the PDF's signature(s)."""
    # A signature's /Contents is a long hex string; a page's is a reference.
    blobs = re.findall(rb"/Contents\s*<([0-9A-Fa-f\s]{2000,})>", pdf)
    certs: list[x509.Certificate] = []
    for blob in blobs:
        data = bytes.fromhex(re.sub(rb"\s", b"", blob).decode())
        data = data[: _der_length(data)]  # drop the zero padding reserved for it
        certs.extend(pkcs7.load_der_pkcs7_certificates(data))
    return certs


def _cn(name: x509.Name) -> str:
    values = name.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(values[0].value) if values else name.rfc4514_string()


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        return cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return False


def signer_certificate(certs: list[x509.Certificate]) -> x509.Certificate | None:
    """The end-entity certificate issued to UIDAI, not a CA above it."""
    for cert in certs:
        org = cert.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
        is_uidai = any("UNIQUE IDENTIFICATION" in str(o.value).upper() for o in org)
        if is_uidai and not _is_ca(cert):
            return cert
    return None


def file_name_for(cert: x509.Certificate) -> str:
    """ds_uidai_<serial-in-name>_<from>-<until>.pem, matching the existing files."""
    token = _cn(cert.subject).split()[-1].lower()
    token = re.sub(r"[^a-z0-9]", "", token) or "new"
    return (
        f"ds_uidai_{token}_{cert.not_valid_before_utc:%Y}-"
        f"{cert.not_valid_after_utc:%Y}.pem"
    )


def verifies_qr(cert: x509.Certificate, qr_image: Path) -> tuple[bool, str]:
    """Whether this certificate validates the signature on a QR image."""
    from app.rules.aadhaar_qr import read_aadhaar_qr
    from app.rules.uidai_signature import SignerKey, verify_qr_signature

    qr, note = read_aadhaar_qr(qr_image.read_bytes())
    if qr is None:
        return False, f"could not read a QR from {qr_image.name}: {note[:80]}"
    key = SignerKey(
        name=_cn(cert.subject),
        filename="candidate",
        public_key=cert.public_key(),  # type: ignore[arg-type]
        valid_from=cert.not_valid_before_utc,
        valid_until=cert.not_valid_after_utc,
    )
    verdict = verify_qr_signature(qr, (key,))
    when = f"{verdict.generated_at:%d %b %Y}" if verdict.generated_at else "undated"
    return verdict.outcome == "verified", f"QR generated {when}: {verdict.outcome}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf", type=Path, help="An e-Aadhaar PDF (no password needed)")
    parser.add_argument("--qr", type=Path, help="Image of a QR signed in the same period")
    parser.add_argument("--write", action="store_true", help="Save the certificate")
    parser.add_argument("--out", type=Path, default=CERT_DIR, help="Where to save it")
    args = parser.parse_args()

    certs = signature_certificates(args.pdf.read_bytes())
    if not certs:
        print("No signature certificate found in this PDF.")
        return 1

    print("Certificate chain carried by the PDF signature:")
    for cert in certs:
        role = "CA " if _is_ca(cert) else "   "
        print(
            f"  {role}{_cn(cert.subject)}\n"
            f"       issued by {_cn(cert.issuer)}, valid "
            f"{cert.not_valid_before_utc:%d %b %Y} -> {cert.not_valid_after_utc:%d %b %Y}"
        )

    signer = signer_certificate(certs)
    if signer is None:
        print("\nNo end-entity certificate issued to UIDAI was found.")
        return 1

    name = file_name_for(signer)
    print(f"\nUIDAI signer: {_cn(signer.subject)} -> {name}")

    # Compare certificates by fingerprint, not file bytes: the same certificate
    # written by openssl and by this script differs in line endings and
    # headers, and a byte comparison would install a duplicate.
    installed = set()
    for path in args.out.glob("*.pem") if args.out.is_dir() else ():
        try:
            installed.add(x509.load_pem_x509_certificate(path.read_bytes()).fingerprint(hashes.SHA256()))
        except ValueError:
            continue
    if signer.fingerprint(hashes.SHA256()) in installed:
        print("Already installed. Nothing to do.")
        return 0
    pem = signer.public_bytes(serialization.Encoding.PEM)

    if args.qr is None:
        print("\nNot written: pass --qr with an image of a QR from the same period to prove it.")
        return 0

    ok, detail = verifies_qr(signer, args.qr)
    print(f"Check against {args.qr.name}: {detail}")
    if not ok:
        print("Not written: this certificate did not verify the QR.")
        return 1

    if not args.write:
        print("Verified. Re-run with --write to install it.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / name).write_bytes(pem)
    print(f"Written to {args.out / name}. Add it to the table in {args.out / 'README.md'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
