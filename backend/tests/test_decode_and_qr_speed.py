"""
Decoding once, and reading QRs cheapest-route-first.

Both changes exist for speed, and both could quietly break correctness: a
shared decoded array that one stage edits and the next inherits, or a QR that
used to be read and no longer is. These pin the properties that keep them safe.
QR images are generated here, so no identity document is involved.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.core import imaging
from app.rules import aadhaar_qr


def png(image: np.ndarray) -> bytes:
    return cv2.imencode(".png", image)[1].tobytes()


def qr_image(text: str = "VERISHIELD-TEST", module_px: int = 8, canvas: int = 900) -> np.ndarray:
    """A clean generated QR on a white page, large enough to read at native size."""
    matrix = cv2.QRCodeEncoder.create().encode(text)
    code = cv2.resize(matrix, None, fx=module_px, fy=module_px, interpolation=cv2.INTER_NEAREST)
    page = np.full((canvas, canvas), 255, np.uint8)
    y = x = (canvas - code.shape[0]) // 2
    page[y : y + code.shape[0], x : x + code.shape[1]] = code
    return cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)


@pytest.fixture(autouse=True)
def fresh_caches():
    imaging.clear_decode_cache()
    aadhaar_qr._PAYLOAD_CACHE.clear()
    yield
    imaging.clear_decode_cache()
    aadhaar_qr._PAYLOAD_CACHE.clear()


# ------------------------------------------------------------ decode cache --


class TestDecodeCache:
    def test_the_same_bytes_are_decoded_once(self, monkeypatch):
        calls = []
        real = imaging._decode_uncached
        monkeypatch.setattr(imaging, "_decode_uncached", lambda data: calls.append(1) or real(data))
        data = png(np.zeros((40, 60, 3), np.uint8))
        for _ in range(5):  # ingest, OCR, forensics, face, QR
            imaging.decode_image(data)
        assert len(calls) == 1

    def test_every_caller_gets_its_own_copy(self):
        data = png(np.zeros((40, 60, 3), np.uint8))
        first = imaging.decode_image(data)
        first[:] = 255  # one stage drawing on its image...
        second = imaging.decode_image(data)
        assert second.max() == 0  # ...must not reach the next one

    def test_an_empty_file_is_refused_with_a_reason(self):
        with pytest.raises(imaging.UnsupportedImageError, match="empty"):
            imaging.decode_image(b"")

    def test_the_cache_stays_small(self, monkeypatch):
        calls = []
        real = imaging._decode_uncached
        monkeypatch.setattr(imaging, "_decode_uncached", lambda data: calls.append(1) or real(data))
        images = [png(np.full((10, 10, 3), v, np.uint8)) for v in (10, 20, 30)]
        for data in images:
            imaging.decode_image(data)
        imaging.decode_image(images[0])  # evicted by the third
        assert len(calls) == 4
        assert len(imaging._DECODE_CACHE) == imaging._DECODE_CACHE_SIZE


# -------------------------------------------------------------- QR routes --


class TestQrRoutes:
    def test_a_clear_qr_is_read_at_native_size(self):
        assert aadhaar_qr.extract_qr_payloads(png(qr_image("HELLO")), thorough=False) == [b"HELLO"]

    def test_without_thorough_a_photograph_gets_no_whole_image_upscale(self, monkeypatch):
        seen = []
        monkeypatch.setattr(aadhaar_qr, "_zbar_payloads", lambda img: seen.append(img.shape) or [])
        blank = np.full((1800, 2400, 3), 255, np.uint8)  # camera-sized
        aadhaar_qr.extract_qr_payloads(png(blank), thorough=False)
        # One native pass; a blank page has no QR for the locator to crop to.
        assert seen == [(1800, 2400, 3)]

    def test_a_small_image_gets_the_ladder_even_without_thorough(self, monkeypatch):
        """
        A screenshot or download is where a front's QR sits at one or two
        pixels per module; upscaling is the only route that reads it, and at
        this size it is cheap.
        """
        seen = []
        monkeypatch.setattr(aadhaar_qr, "_zbar_payloads", lambda img: seen.append(img.shape[:2]) or [])
        blank = np.full((600, 800, 3), 255, np.uint8)
        aadhaar_qr.extract_qr_payloads(png(blank), thorough=False)
        assert seen == [(600, 800), (900, 1200), (1200, 1600)]

    def test_thorough_keeps_the_old_whole_image_ladder(self, monkeypatch):
        seen = []
        monkeypatch.setattr(aadhaar_qr, "_zbar_payloads", lambda img: seen.append(img.shape[:2]) or [])
        blank = np.full((600, 800, 3), 255, np.uint8)
        aadhaar_qr.extract_qr_payloads(png(blank), thorough=True)
        assert seen == [(600, 800), (900, 1200), (1200, 1600)]  # 1x, 1.5x, 2x

    def test_a_second_read_of_the_same_image_is_free(self, monkeypatch):
        data = png(qr_image("ONCE"))
        assert aadhaar_qr.extract_qr_payloads(data) == [b"ONCE"]
        monkeypatch.setattr(
            aadhaar_qr, "_zbar_payloads", lambda img: pytest.fail("decoded again")
        )
        # The case cross-check asks again; it must be answered from the cache.
        assert aadhaar_qr.extract_qr_payloads(data) == [b"ONCE"]
        assert aadhaar_qr.extract_qr_payloads(data, thorough=False) == [b"ONCE"]

    def test_a_non_thorough_miss_does_not_answer_a_thorough_question(self, monkeypatch):
        blank = png(np.full((300, 300, 3), 255, np.uint8))
        aadhaar_qr.extract_qr_payloads(blank, thorough=False)
        seen = []
        monkeypatch.setattr(aadhaar_qr, "_zbar_payloads", lambda img: seen.append(1) or [])
        aadhaar_qr.extract_qr_payloads(blank, thorough=True)
        assert seen  # the thorough question was actually asked


class TestZxingRoute:
    def test_without_zxing_the_zbar_routes_still_read(self, monkeypatch):
        monkeypatch.setattr(aadhaar_qr, "zxingcpp", None)
        assert aadhaar_qr.extract_qr_payloads(png(qr_image("FALLBACK")), thorough=False) == [b"FALLBACK"]

    def test_the_rescue_straightens_a_located_qr(self):
        """
        The rescue warps the four corners zxing reports onto a square. Get the
        corner order wrong and every rescue decodes a mirrored or rotated
        nothing; this pins it with a QR photographed at an angle.
        """
        if aadhaar_qr.zxingcpp is None:
            pytest.skip("zxing-cpp not installed")
        flat = cv2.cvtColor(qr_image("TILTED", module_px=6, canvas=400), cv2.COLOR_BGR2GRAY)
        corners = np.float32([[0, 0], [399, 0], [399, 399], [0, 399]])
        seen_at = np.float32([[120, 90], [560, 140], [520, 610], [80, 540]])
        photo = cv2.warpPerspective(
            flat, cv2.getPerspectiveTransform(corners, seen_at), (700, 700), borderValue=255
        )
        assert aadhaar_qr._rescue(photo, [seen_at]) == [b"TILTED"]
