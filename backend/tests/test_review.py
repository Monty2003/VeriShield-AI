"""
Human decisions on verification results.

An in-memory MongoDB stands in for Atlas, so no test decision is ever written
to a real audit trail.

What is pinned here is less "can a decision be saved" than the properties that
make a saved decision worth trusting later: the server, not the reviewer,
records who decided and what the system had said; going against the system
requires a reason; nothing can be edited or deleted; and a person signing off
their own submission is marked as such.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.api.routes import verify as verify_routes
from app.api.routes.review import DecisionRefused, Outcome, build_decision
from app.core.auth import current_user
from app.core.security import Role
from app.core.users import User
from app.main import app
from app.schemas.document import DocumentAnalysis
from app.storage import audit


def person(name: str, role: Role) -> User:
    return User(
        username=name, password_hash="x", role=role, created_at=datetime.now(timezone.utc)
    )


REVIEWER = person("rev", Role.REVIEWER)


def record(decision: str = "manual_review", blocking=(), submitted_by: str | None = "op"):
    return {
        "document_id": "d1",
        "risk": {"decision": decision, "score": 21.0, "blocking_reasons": list(blocking)},
        "submitted_by": submitted_by,
    }


# ------------------------------------------------------------------ policy --


class TestPolicy:
    def test_agreeing_with_a_clean_accept_needs_no_note(self):
        d = build_decision("document", "d1", record("accept"), Outcome.APPROVE, "", REVIEWER)
        assert d["agrees_with_system"] is True
        assert d["note"] == ""

    @pytest.mark.parametrize("outcome", [Outcome.REJECT, Outcome.NEEDS_INFO])
    def test_rejecting_or_asking_for_more_needs_a_reason(self, outcome):
        with pytest.raises(DecisionRefused):
            build_decision("document", "d1", record("reject"), outcome, "", REVIEWER)

    def test_a_token_note_is_not_a_reason(self):
        with pytest.raises(DecisionRefused):
            build_decision("document", "d1", record("reject"), Outcome.REJECT, "no", REVIEWER)

    def test_approving_against_a_reject_needs_a_justification(self):
        with pytest.raises(DecisionRefused, match="reject"):
            build_decision("document", "d1", record("reject"), Outcome.APPROVE, "", REVIEWER)
        d = build_decision(
            "document", "d1", record("reject"), Outcome.APPROVE,
            "Checked the original card in person; OCR misread the number.", REVIEWER,
        )
        assert d["agrees_with_system"] is False

    def test_approving_past_a_blocking_finding_says_which_and_is_marked(self):
        blocked = record("manual_review", blocking=["The QR signature does not verify."])
        with pytest.raises(DecisionRefused, match="QR signature"):
            build_decision("document", "d1", blocked, Outcome.APPROVE, "", REVIEWER)
        d = build_decision(
            "document", "d1", blocked, Outcome.APPROVE,
            "Holder re-presented the card; the second QR scan verified.", REVIEWER,
        )
        assert d["overrides_block"] is True

    def test_approving_an_unblocked_manual_review_takes_no_position_against_the_system(self):
        d = build_decision("document", "d1", record("manual_review"), Outcome.APPROVE, "", REVIEWER)
        # The system deferred, so there is nothing to agree or disagree with.
        assert d["agrees_with_system"] is None
        assert d["overrides_block"] is False

    def test_a_request_for_information_takes_no_position(self):
        d = build_decision(
            "document", "d1", record("reject"), Outcome.NEEDS_INFO,
            "Need the back of the card to read the QR.", REVIEWER,
        )
        assert d["agrees_with_system"] is None

    def test_signing_off_your_own_submission_is_marked_not_refused(self):
        own = build_decision(
            "document", "d1", record("accept", submitted_by="rev"), Outcome.APPROVE, "", REVIEWER
        )
        other = build_decision(
            "document", "d1", record("accept", submitted_by="op"), Outcome.APPROVE, "", REVIEWER
        )
        unknown = build_decision(
            "document", "d1", record("accept", submitted_by=None), Outcome.APPROVE, "", REVIEWER
        )
        assert (own["self_reviewed"], other["self_reviewed"], unknown["self_reviewed"]) == (
            True,
            False,
            None,
        )

    def test_the_system_view_comes_from_the_record(self):
        d = build_decision("document", "d1", record("reject"), Outcome.REJECT,
                           "Photo on the card is not the holder.", REVIEWER)
        assert (d["system_decision"], d["system_score"], d["reviewer"], d["reviewer_role"]) == (
            "reject", 21.0, "rev", "reviewer",
        )


# --------------------------------------------------------------------- API --


@pytest.fixture
def store(monkeypatch):
    s = audit.AuditStore(client=mongomock.MongoClient(), database="test")
    # Route modules that imported the store by name each need pointing at it.
    monkeypatch.setattr(audit, "audit_store", s)
    monkeypatch.setattr(verify_routes, "audit_store", s)
    return s


def seed(store, collection="documents", key="document_id", subject_id="d1", **risk):
    store._client[store.database][collection].insert_one(
        {
            key: subject_id,
            ("risk" if collection == "documents" else "overall_risk"): {
                "decision": risk.get("decision", "manual_review"),
                "score": 21.0,
                "blocking_reasons": risk.get("blocking", []),
            },
            "submitted_by": risk.get("submitted_by", "op"),
            "recorded_at": "2026-09-19T00:00:00+00:00",
        }
    )


@pytest.fixture
def client():
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def as_user(user: User) -> None:
    app.dependency_overrides[current_user] = lambda: user


class TestDecisionEndpoints:
    def test_an_operator_cannot_decide(self, client, store):
        seed(store)
        as_user(person("op", Role.OPERATOR))
        r = client.post("/api/v1/documents/d1/decisions", json={"outcome": "approve"})
        assert r.status_code == 403
        assert "review:decide" in r.json()["detail"]

    def test_a_reviewer_decides_and_the_server_says_who(self, client, store):
        seed(store, decision="accept")
        as_user(REVIEWER)
        r = client.post("/api/v1/documents/d1/decisions", json={"outcome": "approve"})
        assert r.status_code == 201
        body = r.json()
        assert (body["reviewer"], body["system_decision"], body["agrees_with_system"]) == (
            "rev", "accept", True,
        )

    def test_the_client_cannot_set_what_the_server_owns(self, client, store):
        seed(store, decision="reject")
        as_user(REVIEWER)
        r = client.post(
            "/api/v1/documents/d1/decisions",
            json={"outcome": "approve", "note": "fine by me, trust me",
                  "system_decision": "accept", "reviewer": "someone-else"},
        )
        assert r.status_code == 422

    def test_a_missing_reason_is_refused_with_the_rule(self, client, store):
        seed(store, decision="reject")
        as_user(REVIEWER)
        r = client.post("/api/v1/documents/d1/decisions", json={"outcome": "reject"})
        assert r.status_code == 422
        assert "reason" in r.json()["detail"]

    def test_there_must_be_something_to_decide_on(self, client, store):
        as_user(REVIEWER)
        r = client.post("/api/v1/documents/nope/decisions", json={"outcome": "approve"})
        assert r.status_code == 404

    def test_history_is_kept_and_the_latest_is_current(self, client, store):
        seed(store)
        as_user(REVIEWER)
        client.post("/api/v1/documents/d1/decisions",
                    json={"outcome": "needs_info", "note": "Need the back side for the QR."})
        client.post("/api/v1/documents/d1/decisions",
                    json={"outcome": "approve", "note": "Back side received; QR verified."})
        history = client.get("/api/v1/documents/d1/decisions").json()
        assert [d["outcome"] for d in history["decisions"]] == ["needs_info", "approve"]
        assert history["current"]["outcome"] == "approve"

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_a_decision_cannot_be_edited_or_deleted(self, client, store, method):
        seed(store)
        as_user(person("boss", Role.ADMIN))
        r = getattr(client, method)("/api/v1/documents/d1/decisions")
        assert r.status_code == 405

    def test_an_unreachable_store_refuses_to_pretend(self, client, monkeypatch):
        monkeypatch.setattr(audit, "audit_store", SimpleNamespace(available=False))
        as_user(REVIEWER)
        r = client.post("/api/v1/documents/d1/decisions", json={"outcome": "approve"})
        assert r.status_code == 503
        assert client.get("/api/v1/documents/d1/decisions").json()["available"] is False

    def test_a_case_can_be_decided_too(self, client, store):
        seed(store, collection="cases", key="case_id", subject_id="c1", decision="reject")
        as_user(REVIEWER)
        r = client.post("/api/v1/cases/c1/decisions",
                        json={"outcome": "reject", "note": "Faces on the two documents differ."})
        assert r.status_code == 201
        assert r.json()["subject_type"] == "case"


class TestListingsCarryTheDecision:
    def test_undecided_records_are_the_review_queue(self, client, store):
        seed(store, subject_id="d1")
        seed(store, subject_id="d2")
        as_user(REVIEWER)
        client.post("/api/v1/documents/d1/decisions",
                    json={"outcome": "reject", "note": "Printed DOB contradicts the QR."})
        rows = {d["document_id"]: d for d in client.get("/api/v1/documents/recent").json()["documents"]}
        assert rows["d1"]["review"]["outcome"] == "reject"
        assert rows["d2"]["review"] is None  # still waiting on a person

    def test_cases_listing_carries_it_as_well(self, client, store):
        seed(store, collection="cases", key="case_id", subject_id="c1")
        as_user(REVIEWER)
        client.post("/api/v1/cases/c1/decisions", json={"outcome": "approve"})
        cases = client.get("/api/v1/cases/recent").json()["cases"]
        assert cases[0]["review"]["outcome"] == "approve"


def test_the_submitter_is_recorded_with_the_assessment():
    store = audit.AuditStore(client=mongomock.MongoClient(), database="test")
    store.record_document(DocumentAnalysis(document_id="x", filename="f.jpg"), "fp", submitted_by="op")
    assert store.find_subject("document", "x")["submitted_by"] == "op"
