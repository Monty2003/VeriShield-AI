"""
What /health calls a fault, and what it calls a choice.

The dashboard colours itself from this. An optional store that was never
started is not a degraded server: reporting it as one made the console warn
about a system doing everything it had been asked to do, and taught its user
to ignore the warning that matters.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.storage import audit as audit_module
from app.main import app


class Store:
    def __init__(self, available: bool, error: str = "") -> None:
        self.available = available
        self.error = error


@pytest.fixture
def probe(monkeypatch):
    def set(*, objects: bool, audit: bool):
        # Patched where they live: the route imports them inside the handler.
        monkeypatch.setattr(audit_module, "object_store", Store(objects, "ConnectionRefused"))
        monkeypatch.setattr(audit_module, "audit_store", Store(audit, "ServerSelectionTimeout"))
        return TestClient(app).get("/health").json()

    return set


def test_a_stopped_object_store_is_optional_not_degraded(probe):
    body = probe(objects=False, audit=True)
    assert body["status"] == "ok"
    assert any("retention" in item.lower() for item in body["optional_off"])
    assert not any("object" in item.lower() for item in body["degraded"])


def test_a_stopped_audit_store_is_degraded(probe):
    body = probe(objects=True, audit=False)
    assert any("audit" in item.lower() for item in body["degraded"])


def test_a_whole_server_reports_neither(probe):
    body = probe(objects=True, audit=True)
    assert not any("audit" in item.lower() or "object" in item.lower() for item in body["degraded"])
    assert not any("retention" in item.lower() for item in body["optional_off"])
