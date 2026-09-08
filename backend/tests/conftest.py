"""
Shared test configuration.

The tests must not load heavy models. Once face detection was wired into the
pipeline, every test that calls `analyze_document` began constructing an
InsightFace session and loading five ONNX models -- turning a sub-second suite
into a multi-minute one, and making the tests depend on a model download that
may not be present.

So the default face provider is replaced with the null one for the whole suite.
Tests that genuinely need face behaviour pass a provider explicitly, which also
makes that dependency visible in the test rather than implicit in the
environment.
"""

from __future__ import annotations

import pytest

from app.pipeline.stages import face as face_stage


@pytest.fixture(autouse=True)
def _no_heavy_face_models(monkeypatch):
    """Keep the default face provider inert unless a test asks otherwise."""
    monkeypatch.setattr(
        face_stage, "get_default_provider", lambda *a, **k: face_stage.NullFaceProvider()
    )
