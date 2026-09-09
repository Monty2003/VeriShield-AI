"""
The dedicated face-comparison endpoint.

What matters here is not that a number comes back. It is that the endpoint
never invents an answer it does not have: with no recognition model it must say
identity was NOT checked rather than returning a similarity of zero, and the
middle band must stay a third outcome rather than being rounded to a verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.auth import current_user
from app.core.security import Role
from app.core.users import User
from app.main import app
from app.pipeline.stages import face as face_stage
from app.pipeline.stages.face import DetectedFace, FaceResult
from app.schemas.signals import Region

ENDPOINT = "/api/v1/verify/face"


def _fake_provider(similarity: float, *, face_pixels: int = 200):
    """A provider whose two faces sit at a chosen cosine similarity."""
    angle = np.arccos(np.clip(similarity, -1, 1))
    selfie_vector = np.array([np.cos(angle), np.sin(angle)])

    class FakeProvider:
        name = "fake"
        detection_available = True
        recognition_available = True

        def analyze(self, image_bytes: bytes) -> FaceResult:
            vector = np.array([1.0, 0.0]) if image_bytes == b"document" else selfie_vector
            return FaceResult(
                faces=[
                    DetectedFace(
                        region=Region(x=5, y=7, width=face_pixels, height=face_pixels),
                        confidence=0.91,
                        embedding=vector,
                    )
                ],
                engine="fake",
                detection_available=True,
                recognition_available=True,
            )

    return FakeProvider()


@pytest.fixture
def client(monkeypatch):
    """
    Authenticated client.

    `requires(...)` builds a fresh function per call, so it cannot be overridden
    by key -- but it depends on `current_user`, and overriding that reaches
    every route that gates on a permission.
    """
    app.dependency_overrides[current_user] = lambda: User(
        username="tester",
        password_hash="x",
        role=Role.ADMIN,
        created_at=datetime.now(timezone.utc),
    )
    from app.core import auth as auth_module

    auth_module.reset_rate_limits()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _post(client: TestClient):
    return client.post(
        ENDPOINT,
        files={
            "document": ("doc.jpg", b"document", "image/jpeg"),
            "selfie": ("me.jpg", b"selfie", "image/jpeg"),
        },
    )


class TestFaceMatchEndpoint:
    def test_requires_authentication(self):
        anonymous = TestClient(app, raise_server_exceptions=False)
        response = anonymous.post(
            ENDPOINT,
            files={
                "document": ("doc.jpg", b"document", "image/jpeg"),
                "selfie": ("me.jpg", b"selfie", "image/jpeg"),
            },
        )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "similarity, outcome, code",
        [
            (0.90, "match", "face.match.strong"),
            (0.35, "uncertain", "face.match.uncertain"),
            (0.05, "mismatch", "face.match.mismatch"),
        ],
    )
    def test_three_outcomes_survive_the_api_boundary(
        self, client, monkeypatch, similarity, outcome, code
    ):
        monkeypatch.setattr(
            face_stage, "get_default_provider", lambda *a, **k: _fake_provider(similarity)
        )
        body = _post(client).json()

        assert body["outcome"] == outcome
        assert body["signals"][0]["code"] == code
        # The reported outcome is derived from the signal, so the two can never
        # disagree -- that is the property worth pinning.
        assert body["similarity"] == pytest.approx(similarity, abs=0.02)

    def test_uncertain_withholds_acceptance(self, client, monkeypatch):
        monkeypatch.setattr(
            face_stage, "get_default_provider", lambda *a, **k: _fake_provider(0.35)
        )
        body = _post(client).json()

        assert body["outcome"] == "uncertain"
        assert body["risk"]["decision"] != "accept"
        assert body["risk"]["blocking_codes"] == ["face.match.uncertain"]

    def test_without_a_recognition_model_it_refuses_to_answer(self, client):
        # conftest leaves the default provider null for the whole suite.
        body = _post(client).json()

        assert body["outcome"] == "not_compared"
        # The dangerous failure would be similarity 0.0, which reads as
        # "different people" when the truth is that nothing was compared.
        assert body["similarity"] is None
        assert body["recognition_available"] is False
        assert body["signals"][0]["status"] == "error"
        assert body["signals"][0]["blocking"] is True

    def test_the_model_runs_once_per_image(self, client, monkeypatch):
        """The memo wrapper exists to stop four model passes over two images."""
        calls: list[bytes] = []
        inner = _fake_provider(0.9)

        class CountingProvider:
            name = inner.name
            detection_available = True
            recognition_available = True

            def analyze(self, image_bytes: bytes) -> FaceResult:
                calls.append(image_bytes)
                return inner.analyze(image_bytes)

        monkeypatch.setattr(
            face_stage, "get_default_provider", lambda *a, **k: CountingProvider()
        )
        _post(client)

        assert len(calls) == 2
        assert sorted(set(calls)) == [b"document", b"selfie"]

    def test_reports_both_face_boxes_for_the_overlay(self, client, monkeypatch):
        monkeypatch.setattr(
            face_stage, "get_default_provider", lambda *a, **k: _fake_provider(0.9)
        )
        body = _post(client).json()

        for side in ("document", "selfie"):
            assert body[side]["faces_found"] == 1
            assert body[side]["region"]["width"] == 200
        # These bytes are not a real image, so decoding for dimensions fails and
        # the endpoint reports that rather than raising.
        assert body["document"]["image_width"] is None
