"""
Liveness endpoints.

The exchange is deliberately three steps rather than one upload:

    POST /liveness/start                 -> session id + a challenge to perform
    POST /liveness/{id}/frame            -> one camera frame, repeated
    POST /liveness/{id}/complete         -> the verdict

The challenge is chosen server-side and revealed only when the session opens.
That ordering is the entire security property: a recording made in advance
cannot contain the right action at the right moment, because the attacker did
not know which action would be asked for.

Session storage is in-process, which is fine for one server and wrong for
several -- a session started on one worker would not be found by another.
Redis is already a dependency of this project and is where these belong before
it runs behind more than one process; the limitation is noted here rather than
discovered in production.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.auth import rate_limit, requires
from app.core.users import User

from app.pipeline.stages.liveness import (
    Challenge,
    LivenessSession,
    evaluate,
    observe_frame,
    start_session,
)

router = APIRouter()

# session id -> session. See the module docstring on why this is not Redis yet.
_SESSIONS: dict[str, LivenessSession] = {}

# Enough frames to see an action through, few enough that a client cannot
# stream indefinitely hoping something registers.
MAX_FRAMES = 40


def _get(session_id: str) -> LivenessSession:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown liveness session.")
    if session.expired:
        _SESSIONS.pop(session_id, None)
        raise HTTPException(
            status_code=410,
            detail=(
                "This liveness session has expired. Start a new one -- sessions "
                "are short-lived so a challenge cannot be answered at leisure."
            ),
        )
    return session


@router.post("/liveness/start", dependencies=[Depends(rate_limit("liveness"))])
def liveness_start(
    challenge: Challenge | None = None,
    _user: User = Depends(requires("verify:submit")),
) -> dict[str, object]:
    """
    Open a liveness session.

    `challenge` may be supplied for testing. In normal use it is omitted and
    chosen at random -- a caller who picks their own challenge has removed the
    unpredictability the check depends on.
    """
    session = start_session(challenge)
    _SESSIONS[session.session_id] = session
    return {
        "session_id": session.session_id,
        "challenge": session.challenge.value,
        "prompt": session.prompt,
        "instructions": (
            "Send camera frames to /liveness/{session_id}/frame while performing "
            "the action, then call /complete. Aim for 8-20 frames over a couple "
            "of seconds."
        ),
        "max_frames": MAX_FRAMES,
        "caller_chose_challenge": challenge is not None,
    }


@router.post(
    "/liveness/{session_id}/frame",
    dependencies=[Depends(rate_limit("liveness"))],
)
async def liveness_frame(
    session_id: str,
    file: UploadFile = File(..., description="One camera frame"),
    _user: User = Depends(requires("verify:submit")),
) -> dict[str, object]:
    """Submit one frame. Returns what was observed, so a UI can guide the user."""
    session = _get(session_id)

    if len(session.frames) >= MAX_FRAMES:
        raise HTTPException(
            status_code=429,
            detail=f"This session already holds {MAX_FRAMES} frames.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty frame.")

    observation = observe_frame(len(session.frames), data)
    session.frames.append(observation)

    return {
        "frame": observation.index,
        "face_detected": observation.face_found,
        # Returned so the client can show live feedback -- "hold still", "we
        # can't see your face" -- rather than failing silently at the end.
        "eye_openness": observation.eye_openness,
        "yaw": observation.yaw,
        "pitch": observation.pitch,
        "frames_collected": len(session.frames),
    }


@router.post("/liveness/{session_id}/complete")
def liveness_complete(
    session_id: str, _user: User = Depends(requires("verify:submit"))
) -> dict[str, object]:
    """Evaluate the session and return the verdict with its reasoning."""
    session = _get(session_id)
    signals = evaluate(session)
    _SESSIONS.pop(session_id, None)

    passed = any(s.code == "liveness.challenge_passed" for s in signals)
    return {
        "session_id": session_id,
        "challenge": session.challenge.value,
        "passed": passed,
        "frames_submitted": len(session.frames),
        "frames_with_face": len(session.faces_seen),
        "signals": [s.model_dump(mode="json") for s in signals],
        "limitations": (
            "A passed check establishes that a face performed a requested action "
            "on demand, which a photograph or a still screen cannot do. It does "
            "not establish that the video stream itself is genuine: frames "
            "injected directly into the camera pipeline would not be caught. "
            "Spoof-detection accuracy has not been measured -- that needs "
            "recordings of real presentation attacks."
        ),
    }


@router.get("/liveness/challenges")
def liveness_challenges() -> dict[str, object]:
    """The available challenges, for a client that wants to render its own UI."""
    from app.pipeline.stages.liveness import CHALLENGE_PROMPTS

    return {
        "challenges": [
            {"challenge": c.value, "prompt": CHALLENGE_PROMPTS[c]} for c in Challenge
        ]
    }
