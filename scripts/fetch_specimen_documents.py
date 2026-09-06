"""
Download official specimen passport data pages from Wikimedia Commons.

What these are for
------------------
Specimen documents are published BY governments as public reference material.
They carry a real ICAO layout and a real machine-readable zone, but they
describe no actual person -- which makes them the right thing to test an
identity pipeline against, and the wrong thing to feel uneasy about storing.

They are good for:
  * MRZ parser validation across real-world variety -- different issuing
    countries, fonts, print quality and layouts.
  * OCR and classifier validation on genuine document structure.

They are NOT suitable for forensic calibration, and the script puts them
somewhere separate for that reason. Every image here has been through
Wikimedia's resizing and re-encoding pipeline, which destroys precisely the
compression history and sensor noise that ELA and noise analysis measure. A
detector calibrated on these would learn the properties of image hosting
rather than the properties of tampering.

Forensic calibration needs images straight off your own camera, in
backend/data/datasets/raw/. See backend/data/datasets/README.md.

Licensing
---------
Commons images carry per-file licences. The manifest records the licence,
author and source URL for each download, so attribution is possible and the
provenance of anything used in a report is traceable.

Usage
-----
    python scripts/fetch_specimen_documents.py
    python scripts/fetch_specimen_documents.py --limit 20
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "backend" / "data" / "datasets" / "external" / "specimens" / "passport"

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Identify the client properly -- Wikimedia asks for this and throttles
# anonymous scrapers that do not provide it.
USER_AGENT = (
    "VeriShield-AI/0.1 (identity verification research; "
    "https://github.com/; contact via repository)"
)

SEARCH_TERMS = (
    "specimen passport data page",
    "passport specimen biodata page",
    "passport information page specimen",
)

# Licences we accept. Anything more restrictive is skipped rather than
# downloaded and quietly used -- a project about document authenticity should
# not be careless about provenance.
ACCEPTABLE_LICENCE = re.compile(
    r"(cc[-\s]?by|cc[-\s]?0|public\s*domain|pd-|gfdl)", re.IGNORECASE
)

# Below this, the MRZ band is unreadable and the file is not worth keeping.
MIN_WIDTH = 700


def _api(params: dict) -> dict:
    """Call the Commons API and return parsed JSON."""
    params = {**params, "format": "json"}
    url = f"{COMMONS_API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def search_files(term: str, limit: int) -> list[str]:
    """Find File: pages matching a search term."""
    data = _api(
        {
            "action": "query",
            "list": "search",
            "srsearch": term,
            "srnamespace": 6,  # File:
            "srlimit": limit,
        }
    )
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def file_info(titles: list[str]) -> dict[str, dict]:
    """Fetch URL, size and licence metadata for a batch of files."""
    data = _api(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
        }
    )
    out: dict[str, dict] = {}
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [None])[0]
        if info:
            out[page["title"]] = info
    return out


def _meta(info: dict, key: str, default: str = "") -> str:
    """Pull a value out of Commons extmetadata, stripping any HTML."""
    raw = info.get("extmetadata", {}).get(key, {}).get("value", default)
    return re.sub(r"<[^>]+>", "", str(raw)).strip()


def safe_filename(title: str) -> str:
    """Turn a Commons title into a filesystem-safe name."""
    name = title.replace("File:", "").strip()
    name = re.sub(r"[^\w.\- ]", "_", name).replace(" ", "_")
    return name[:120]


def download(url: str, dest: Path) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download public specimen passport pages from Wikimedia Commons."
    )
    parser.add_argument("--limit", type=int, default=15, help="Max files to download.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    titles: list[str] = []
    for term in SEARCH_TERMS:
        try:
            titles.extend(search_files(term, limit=args.limit))
        except Exception as exc:  # noqa: BLE001 -- report and continue to next term
            print(f"  search failed for {term!r}: {exc}")
        time.sleep(0.4)

    # De-duplicate, preserving discovery order.
    seen: set[str] = set()
    titles = [t for t in titles if not (t in seen or seen.add(t))]
    if not titles:
        print("No specimen files found. Commons may be unreachable.")
        return 1

    print(f"Found {len(titles)} candidate files. Fetching metadata...\n")

    records: list[dict] = []
    downloaded = 0
    skipped: list[str] = []

    for batch_start in range(0, len(titles), 20):
        batch = titles[batch_start : batch_start + 20]
        try:
            infos = file_info(batch)
        except Exception as exc:  # noqa: BLE001
            print(f"  metadata fetch failed: {exc}")
            continue

        for title, info in infos.items():
            if downloaded >= args.limit:
                break

            licence = _meta(info, "LicenseShortName") or _meta(info, "License")
            width = int(info.get("width") or 0)
            mime = info.get("mime", "")

            if not mime.startswith("image/"):
                skipped.append(f"{title}: not an image ({mime})")
                continue
            if width < MIN_WIDTH:
                skipped.append(f"{title}: only {width}px wide, MRZ would be unreadable")
                continue
            if not ACCEPTABLE_LICENCE.search(licence):
                skipped.append(f"{title}: licence not clearly reusable ({licence!r})")
                continue

            dest = OUT_DIR / safe_filename(title)
            try:
                size = download(info["url"], dest)
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{title}: download failed ({exc})")
                continue

            downloaded += 1
            print(f"  [{downloaded:>2}] {dest.name}  ({width}px, {size / 1024:.0f} KB, {licence})")

            records.append(
                {
                    "file": dest.name,
                    "title": title,
                    "source_url": info.get("descriptionurl", ""),
                    "image_url": info.get("url", ""),
                    "width": width,
                    "height": int(info.get("height") or 0),
                    "licence": licence,
                    "author": _meta(info, "Artist"),
                    "credit": _meta(info, "Credit"),
                }
            )
            time.sleep(0.3)

    manifest = OUT_DIR / "manifest.json"
    manifest.write_text(json.dumps(records, indent=2), encoding="utf-8")

    print(f"\nDownloaded : {downloaded}")
    print(f"Skipped    : {len(skipped)}")
    for reason in skipped[:8]:
        print(f"  - {reason}")
    print(f"\nOutput   : {OUT_DIR}")
    print(f"Manifest : {manifest}  (licence and attribution per file)")
    print(
        "\nThese are for MRZ/OCR/classifier validation only. They are NOT valid "
        "for forensic calibration -- Wikimedia re-encodes every upload, which "
        "erases the compression history ELA depends on. Use your own camera "
        "captures in backend/data/datasets/raw/ for that."
    )
    return 0 if downloaded else 1


if __name__ == "__main__":
    sys.exit(main())
