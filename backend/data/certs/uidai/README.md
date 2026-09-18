# UIDAI document-signer certificates

Public keys used to verify the RSA signature on an Aadhaar Secure QR.

UIDAI signs every Secure QR -- on the physical card, the PVC card, the e-Aadhaar
PDF and mAadhaar -- with a *document-signer* (DS) key. The last 256 bytes of the
decompressed QR payload are an RSASSA-PKCS1-v1_5 / SHA-256 signature over every
byte before them. A valid signature means the QR is exactly what UIDAI issued;
it cannot be produced without UIDAI's private key.

These are **public** certificates. They contain no personal data and are safe to
commit.

## What is here

| File | Certificate CN | Valid | How it was obtained | How it was proven |
|---|---|---|---|---|
| `ds_uidai_4_2017-2020.pem` | DS UNIQUE IDENTIFICATION AUTHORITY OF INDIA 4 | 2017-06-08 -> 2020-06-07 | Bundled with the open-source `aadhaar-offline-kyc` crate (github.com/wearekesk/identity-crates) | Issued by eMudhra Sub CA for Document Signer. No local QR from this period to test against yet |
| `ds_uidai_01_2020-2023.pem` | DS UIDAI 01 | 2020-05-27 -> 2023-05-27 | Same crate | Verified a real Secure QR generated 25 Jul 2021 |
| `ds_uidai_06_2026-2029.pem` | DS Unique Identification Authority of India 06 | 2026-02-03 -> 2029-02-03 | Extracted from the digital signature of an e-Aadhaar PDF downloaded Sep 2026 | Chains to *CCA India 2022* (the Government of India root). Verified the V5 QR inside that same e-Aadhaar |
| `recovered_uidai_2024-2025.json` | *(no certificate)* | observed 2024-07 -> 2025-07 | **Recovered** from the signatures of two genuine QRs (V3 Jul 2024, V5 Jul 2025) -- see *Recovered keys* below | Both QRs verify under it with the vetted library; none of the certified keys verifies either |

Expired certificates are kept on purpose. Cards and PDFs signed years ago are
still in circulation and still carry those signatures; the key remains the
right way to check them. Validity windows are used only to decide which key a
QR *should* have been signed with -- see below.

## The gap: May 2023 -> Feb 2026

No certificate for this period is held. Its key has been **recovered** from two
genuine QRs (Jul 2024 and Jul 2025), which closes the middle of the gap: any QR
signed with that key now verifies, whatever its date.

What remains open is the tampering rule at either end. The recovered key is
only *known* to have been in use from Jul 2024 to Jul 2025, so a QR dated

- May 2023 -> Jun 2024, or
- Aug 2025 -> Feb 2026

that fails verification is still reported as a **warning**, not as tampering --
UIDAI may have used another key then. A forger who knows this could date a
fabricated QR into those months to get only a warning. Each further genuine QR
from those months, verified against the recovered key, extends its observed
span and narrows the opening.

## How the verifier uses the QR's date

Every Secure QR carries a *reference id*: the last four digits of the Aadhaar
number followed by the time the QR was generated (`YYYYMMDDHHMMSSsss`). That
timestamp is inside the signed bytes, so it cannot be changed without breaking
the signature.

| Signature | QR date | Outcome |
|---|---|---|
| verifies against any held key | any | **pass** |
| verifies against none | inside a held key's window | **fail** -- that key should have verified it |
| verifies against none | in the gap, or unreadable | **warning** -- the right key may simply not be held |

## Recovered keys (no certificate)

When no certificate for a period can be found, the key can still be recovered
from the signatures of genuine QRs it made. For each signature `s` over a
message whose PKCS#1 v1.5 encoding is `m`, `N` divides `s^e - m`; two
signatures by the same key give `N = gcd(s1^e - m1, s2^e - m2)` after small
factors are removed. Signatures from *different* keys give a tiny gcd, which is
itself the answer that they were not made by one key.

A recovered key is stored as JSON beside the certificates:

    {
      "name": "...",
      "exponent": 65537,
      "modulus_hex": "...",
      "modulus_sha256": "...",      # a mismatch makes the loader ignore the file
      "observed_from": "YYYY-MM-DD",
      "observed_until": "YYYY-MM-DD",
      "provenance": "..."
    }

It differs from a certified key in three ways, all visible to the reviewer:

- **Trust.** A certificate ties the key to UIDAI through a government CA
  chain. A recovered key is tied to UIDAI only through the QRs it came from,
  so those must be genuine cards. Verification against it is reported as
  *recovered key, no certificate* at confidence 0.9 rather than 0.99.
- **Window.** It has no validity dates, only the span over which it was seen
  signing. That span is used exactly -- no margin -- because the key is known
  to have been in use at both ends. A QR can still *verify* against the key
  outside that span; only the "should have verified" (tampering) rule is
  limited to it, and the span widens as more genuine QRs are seen.
- **Provenance.** Record which QRs it was recovered from in `provenance`, by
  format and month only -- never anything about whose cards they were.

## Adding a certificate when UIDAI rotates

UIDAI signs the e-Aadhaar PDF with the same DS key it uses for the QR, and the
PDF signature embeds the full certificate. So any freshly downloaded e-Aadhaar
yields the current key -- **no password needed**, because the PDF standard
exempts the signature from encryption:

    python scripts/extract_uidai_cert.py path/to/EAadhaar.pdf

The script prints the certificate chain and writes the signer certificate here.
Then run the tests. Do not add a certificate that has not verified a real QR
from its own period.

Note: re-downloading an old Aadhaar today gives a PDF signed with *today's* key.
To recover an older key you need a PDF that was actually downloaded back then.
