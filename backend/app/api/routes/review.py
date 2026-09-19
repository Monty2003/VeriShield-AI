"""
Human decisions on verification results.

The system never decides. It recommends -- accept, manual review, reject --
and hands the evidence to a person. Until now that person's decision went
nowhere: the product's central promise ended at a recommendation. These
endpoints record it.

    POST /documents/{document_id}/decisions   record a decision on a document
    GET  /documents/{document_id}/decisions   its decision history
    POST /cases/{case_id}/decisions           the same for a case
    GET  /cases/{case_id}/decisions

What a decision records, and who supplies it:

  * outcome and note           -- the reviewer
  * who decided, and their role -- the server, from the token; never the body
  * what the system recommended -- the server, from the stored audit record,
                                  so it cannot be misstated by the person
                                  overriding it
  * whether it agreed with the system, overrode a blocking finding, or was
    made by the same person who submitted the document -- derived, so the
    audit trail says so without anyone having to volunteer it

Decisions are append-only. A new one supersedes the last for display; the old
one stays. There is deliberately no endpoint to edit or delete one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import requires
from app.core.users import User
from app.storage import audit

router = APIRouter()

# Long enough that "ok" or "no" cannot pass for a reason, short enough not to
# get in the way of a reviewer who has one.
MIN_NOTE_CHARS = 10


class Outcome(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_INFO = "needs_info"


class DecisionRequest(BaseModel):
    # Anything beyond these two fields is refused outright. A client sending
    # "system_decision" or "reviewer" is attempting to set something the
    # server owns, and silently ignoring it would hide that.
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    note: str = Field("", max_length=2000)


class DecisionRefused(ValueError):
    """The decision is not acceptable as submitted; the message says why."""


def build_decision(
    subject_type: str,
    subject_id: str,
    record: dict[str, Any],
    outcome: Outcome,
    note: str,
    reviewer: User,
) -> dict[str, Any]:
    """
    Turn a reviewer's choice into the record that is kept.

    Pure, so the rules can be tested without a database. Raises
    DecisionRefused when a note is required and missing.
    """
    risk = record.get("risk") or record.get("overall_risk") or {}
    system = risk.get("decision")
    blocked = bool(risk.get("blocking_reasons"))
    note = note.strip()

    # Agreement is only meaningful where the system took a position. On a
    # manual_review it deferred, and asking for more information takes no
    # position either.
    if outcome is Outcome.NEEDS_INFO or system not in ("accept", "reject"):
        agrees = None
    else:
        agrees = (outcome is Outcome.APPROVE) == (system == "accept")

    overrides_block = outcome is Outcome.APPROVE and blocked

    # A reason is required whenever the decision is one somebody will later
    # need to understand: every rejection and every request for information,
    # and any approval that goes against the system or past a finding that
    # withheld acceptance. Agreeing with a clean accept needs none.
    if outcome is Outcome.REJECT:
        why = (
            "A rejection needs a reason. It is a decision about a person, and "
            "whoever reads the audit trail later has to be able to see why."
        )
    elif outcome is Outcome.NEEDS_INFO:
        why = "Say what information is needed, so the next person knows what to ask for."
    elif overrides_block:
        why = (
            "This approval goes past a blocking finding "
            f"({'; '.join(risk.get('blocking_reasons', []))[:160]}). Say why it "
            "does not apply here."
        )
    elif agrees is False:
        why = (
            f"The system recommended '{system}'. Approving against that needs a "
            "justification in the note."
        )
    else:
        why = ""
    if why and len(note) < MIN_NOTE_CHARS:
        raise DecisionRefused(f"{why} (at least {MIN_NOTE_CHARS} characters)")

    submitted_by = record.get("submitted_by")
    return {
        "decision_id": str(uuid.uuid4()),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "outcome": outcome.value,
        "note": note,
        "reviewer": reviewer.username,
        "reviewer_role": reviewer.role.value,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "system_decision": system,
        "system_score": risk.get("score"),
        "system_blocked": blocked,
        "agrees_with_system": agrees,
        "overrides_block": overrides_block,
        # Not refused: a one-person deployment has no one else to ask. Recorded
        # instead, so a sign-off without a second pair of eyes is visible as
        # exactly that. Older records carry no submitter, so this is unknown
        # rather than false for them.
        "self_reviewed": None if submitted_by is None else submitted_by == reviewer.username,
    }


def _record(subject_type: str, subject_id: str, body: DecisionRequest, user: User) -> dict:
    store = audit.audit_store
    if not store.available:
        raise HTTPException(
            status_code=503,
            detail=(
                "The audit store is unreachable, so the decision cannot be "
                "recorded -- and a decision that is not recorded has not been made."
            ),
        )
    record = store.find_subject(subject_type, subject_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {subject_type} {subject_id!r} in the audit trail to decide on.",
        )
    try:
        decision = build_decision(subject_type, subject_id, record, body.outcome, body.note, user)
    except DecisionRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not store.record_decision(decision):
        raise HTTPException(status_code=503, detail="The decision could not be written.")
    return decision


def _history(subject_type: str, subject_id: str) -> dict[str, object]:
    store = audit.audit_store
    if not store.available:
        return {"available": False, "decisions": [], "current": None}
    decisions = store.decisions_for(subject_type, subject_id)
    return {
        "available": True,
        "decisions": decisions,
        "current": decisions[-1] if decisions else None,
    }


@router.post("/documents/{document_id}/decisions", status_code=201)
def decide_document(
    document_id: str,
    body: DecisionRequest,
    user: User = Depends(requires("review:decide")),
) -> dict:
    return _record("document", document_id, body, user)


@router.get("/documents/{document_id}/decisions")
def document_decisions(
    document_id: str, _user: User = Depends(requires("audit:read"))
) -> dict[str, object]:
    return _history("document", document_id)


@router.post("/cases/{case_id}/decisions", status_code=201)
def decide_case(
    case_id: str,
    body: DecisionRequest,
    user: User = Depends(requires("review:decide")),
) -> dict:
    return _record("case", case_id, body, user)


@router.get("/cases/{case_id}/decisions")
def case_decisions(
    case_id: str, _user: User = Depends(requires("audit:read"))
) -> dict[str, object]:
    return _history("case", case_id)
