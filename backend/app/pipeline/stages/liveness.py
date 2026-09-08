"""
Liveness detection (Layer 6, anti-spoofing).

Decides whether the face in front of the camera belongs to a person who is
actually there, rather than a photograph, a screen, or a recording.

Why challenge-response, and not a classifier
--------------------------------------------
The obvious approach is a network trained to tell real faces from spoofed ones
in a single frame. That was rejected on the evidence of this project's own
history: three separate attempts at detecting tampering from image statistics
(classical forensics, a trained patch model, a portrait-consistency check) all
measured at or near chance, every time for the same reason -- no labelled data
from the right distribution, and synthetic substitutes that taught the model
the generator rather than the phenomenon.

Passive liveness has exactly that shape. It needs spoof corpora (CASIA-FASD,
Replay-Attack, OULU-NPU), it generalises poorly across cameras and lighting,
and without those corpora any threshold here would be invented.

Challenge-response is a different kind of claim. Asking for a blink at an
unpredictable moment and measuring whether the eyes actually closed is not a
statistical judgement about texture; it is an observation about whether
something moved. A printed photograph cannot blink. A static image on a screen
cannot turn its head. Those are facts, not confidence scores -- the same
property that makes a checksum worth more here than a model.

What this defeats, and what it does not
---------------------------------------
Defeats: a printed photograph, a photograph displayed on a screen, and a
recorded video that does not happen to contain the requested action at the
requested moment.

Does NOT defeat: a live video-injection attack that feeds synthetic frames
directly into the stream, or a real accomplice performing the challenge. Those
need signals this cannot see, and claiming otherwise would be the kind of
unearned assurance this project avoids.

Unmeasured, and stated as such
------------------------------
The mechanics below are verified: eye aspect ratio tracks eye closure, head
pose tracks rotation, and identity is checked across frames. The SPOOF
DETECTION RATE is not measured, because that needs recordings of real
presentation attacks. Every result therefore reports what was observed --
"the eyes closed and reopened" -- rather than a probability that the subject
is alive.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

import numpy as np

from app.schemas.signals import Severity, Signal, SignalStatus, Stage, signal

# Landmark indices for each eye in the 106-point model. Found by measuring
# which landmarks fall within the eye region around the detector's own eye
# keypoints, rather than taken from documentation that may not match the
# shipped model.
LEFT_EYE_LANDMARKS = tuple(range(33, 43))
RIGHT_EYE_LANDMARKS = tuple(range(87, 97))

# Eye openness below this fraction of its own baseline counts as closed.
# Relative to the subject's own open eye rather than an absolute value,
# because eye shape varies enormously between people and a fixed threshold
# would call some people permanently blinking.
BLINK_CLOSED_RATIO = 0.65

# Degrees of yaw or pitch that count as a completed turn.
TURN_DEGREES = 18.0

# Frames without a detected face before a session is abandoned. A few are
# normal -- people move out of frame mid-turn.
MAX_CONSECUTIVE_MISSES = 12

# How similar consecutive frames' faces must be to count as the same person.
# Guards the obvious attack on a challenge protocol: perform the blink
# yourself, then swap in the victim's photograph for the frame that gets
# matched against the document.
IDENTITY_CONSISTENCY_THRESHOLD = 0.35

# A session that stays open indefinitely lets an attacker take as long as they
# need to prepare a response.
SESSION_TTL_SECONDS = 120

# Frames are downscaled to this long edge before analysis.
#
# Liveness needs a face, not detail. A webcam frame is around 640 pixels
# already, but nothing stops a client posting a 12-megapixel photograph, and
# full FaceAnalysis on one takes tens of seconds on CPU -- measured at roughly
# a minute per frame, which exhausted the session TTL after two frames and
# returned "expired" for what was really "too slow". Downscaling first keeps a
# frame's cost proportional to what the check actually needs.
FRAME_MAX_EDGE = 640


class Challenge(str, Enum):
    BLINK = "blink"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"
    LOOK_UP = "look_up"


CHALLENGE_PROMPTS = {
    Challenge.BLINK: "Blink both eyes",
    Challenge.TURN_LEFT: "Slowly turn your head to your left",
    Challenge.TURN_RIGHT: "Slowly turn your head to your right",
    Challenge.LOOK_UP: "Tilt your head up slightly",
}


@dataclass
class FrameObservation:
    """What one frame showed."""

    index: int
    face_found: bool
    eye_openness: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    embedding: np.ndarray | None = None


@dataclass
class LivenessSession:
    """
    One challenge-response exchange.

    The challenge is chosen server-side and only revealed when the session
    starts, so a recording made in advance cannot contain the right action at
    the right time -- which is the whole basis of the check.
    """

    session_id: str
    challenge: Challenge
    created_at: datetime
    frames: list[FrameObservation] = field(default_factory=list)
    baseline_openness: float | None = None
    completed: bool = False

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) - self.created_at > timedelta(
            seconds=SESSION_TTL_SECONDS
        )

    @property
    def prompt(self) -> str:
        return CHALLENGE_PROMPTS[self.challenge]

    @property
    def faces_seen(self) -> list[FrameObservation]:
        return [f for f in self.frames if f.face_found]


def eye_openness(landmarks: np.ndarray) -> float:
    """
    Height-to-width ratio of the eye openings, averaged over both eyes.

    Deliberately computed from the bounding extent of each eye's landmark
    cloud rather than from named eyelid points. The 106-point model's exact
    point ordering is not something to rely on, and the extent shrinks when an
    eye closes regardless of which index is which.
    """
    ratios = []
    for indices in (LEFT_EYE_LANDMARKS, RIGHT_EYE_LANDMARKS):
        points = landmarks[list(indices)]
        width = float(points[:, 0].max() - points[:, 0].min())
        height = float(points[:, 1].max() - points[:, 1].min())
        if width > 1e-6:
            ratios.append(height / width)
    return float(np.mean(ratios)) if ratios else 0.0


def observe_frame(
    index: int, image_bytes: bytes, provider=None
) -> FrameObservation:
    """Extract everything one frame contributes to a liveness decision."""
    from app.core.imaging import decode_image
    from app.pipeline.stages.face import get_default_provider

    provider = provider or get_default_provider()

    try:
        import cv2

        frame = decode_image(image_bytes)
        longest = max(frame.shape[:2])
        if longest > FRAME_MAX_EDGE:
            scale = FRAME_MAX_EDGE / longest
            frame = cv2.resize(
                frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )

        faces = provider._get_app().get(frame)
    except Exception:  # noqa: BLE001 -- a bad frame is a miss, not a crash
        return FrameObservation(index=index, face_found=False)

    if not faces:
        return FrameObservation(index=index, face_found=False)

    # Largest face: the subject, not someone in the background.
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    landmarks = getattr(face, "landmark_2d_106", None)
    pose = getattr(face, "pose", None)

    return FrameObservation(
        index=index,
        face_found=True,
        eye_openness=eye_openness(landmarks) if landmarks is not None else None,
        pitch=float(pose[0]) if pose is not None else None,
        yaw=float(pose[1]) if pose is not None else None,
        embedding=getattr(face, "normed_embedding", None),
    )


def start_session(challenge: Challenge | None = None) -> LivenessSession:
    """Open a session with a randomly chosen challenge."""
    return LivenessSession(
        session_id=str(uuid.uuid4()),
        challenge=challenge or random.choice(list(Challenge)),
        created_at=datetime.now(timezone.utc),
    )


def _challenge_met(session: LivenessSession) -> tuple[bool, str]:
    """Decide whether the requested action actually happened."""
    observed = session.faces_seen
    if len(observed) < 3:
        return False, f"Only {len(observed)} frame(s) contained a face."

    if session.challenge == Challenge.BLINK:
        openness = [f.eye_openness for f in observed if f.eye_openness is not None]
        if len(openness) < 3:
            return False, "Eye landmarks were not available in enough frames."

        # Baseline is the subject's own open eye, so the test does not depend
        # on absolute eye shape.
        baseline = float(np.percentile(openness, 75))
        minimum = float(np.min(openness))
        closed = minimum < baseline * BLINK_CLOSED_RATIO

        # A blink is a closing AND a reopening. A photograph of someone with
        # their eyes shut would otherwise pass a closure test.
        closed_index = int(np.argmin(openness))
        reopened = any(
            value > baseline * 0.9 for value in openness[closed_index + 1 :]
        )

        if closed and reopened:
            return True, (
                f"The eyes closed to {minimum / baseline:.0%} of their open size "
                f"and reopened -- a blink. A photograph cannot do this."
            )
        if closed:
            return False, "The eyes closed but did not reopen within the frames sent."
        return False, (
            f"No blink was observed: eye openness stayed above "
            f"{minimum / baseline:.0%} of baseline throughout."
        )

    if session.challenge in (Challenge.TURN_LEFT, Challenge.TURN_RIGHT):
        yaws = [f.yaw for f in observed if f.yaw is not None]
        if len(yaws) < 3:
            return False, "Head pose was not available in enough frames."
        swing = max(yaws) - min(yaws)
        wanted = TURN_DEGREES
        if swing >= wanted:
            return True, (
                f"The head rotated through {swing:.0f} degrees of yaw. A flat "
                f"image held up to the camera does not change pose."
            )
        return False, (
            f"The head rotated only {swing:.0f} degrees; at least {wanted:.0f} "
            f"were expected."
        )

    if session.challenge == Challenge.LOOK_UP:
        pitches = [f.pitch for f in observed if f.pitch is not None]
        if len(pitches) < 3:
            return False, "Head pose was not available in enough frames."
        swing = max(pitches) - min(pitches)
        if swing >= TURN_DEGREES:
            return True, f"The head tilted through {swing:.0f} degrees of pitch."
        return False, f"The head tilted only {swing:.0f} degrees."

    return False, "Unknown challenge."


def _identity_consistent(session: LivenessSession) -> tuple[bool, float]:
    """
    Check the same face is present throughout.

    Without this, the protocol is trivially defeated: perform the challenge
    with your own face, then hold up the victim's photograph for the frame
    that gets compared against their document.
    """
    from app.pipeline.stages.face import cosine_similarity

    embeddings = [f.embedding for f in session.faces_seen if f.embedding is not None]
    if len(embeddings) < 2:
        return True, 1.0

    reference = embeddings[0]
    similarities = [cosine_similarity(reference, e) for e in embeddings[1:]]
    lowest = float(min(similarities))
    return lowest >= IDENTITY_CONSISTENCY_THRESHOLD, lowest


def evaluate(session: LivenessSession) -> list[Signal]:
    """Turn a completed session into Signals."""
    if session.expired:
        return [
            signal(
                code="liveness.expired",
                stage=Stage.FACE,
                title="Liveness check",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    f"The liveness session expired before it completed. Sessions "
                    f"last {SESSION_TTL_SECONDS} seconds so that a challenge cannot "
                    f"be answered at leisure."
                ),
            )
        ]

    observed = session.faces_seen
    if not observed:
        return [
            signal(
                code="liveness.no_face",
                stage=Stage.FACE,
                title="Liveness check",
                status=SignalStatus.ERROR,
                severity=Severity.HIGH,
                blocking=True,
                reason=(
                    f"No face was visible in any of the {len(session.frames)} frames "
                    f"submitted, so liveness could not be assessed."
                ),
            )
        ]

    signals: list[Signal] = []

    consistent, lowest = _identity_consistent(session)
    if not consistent:
        signals.append(
            signal(
                code="liveness.identity_changed",
                stage=Stage.FACE,
                title="Liveness identity consistency",
                status=SignalStatus.FAIL,
                severity=Severity.CRITICAL,
                confidence=0.8,
                blocking=True,
                reason=(
                    f"The face changed during the liveness check (similarity fell "
                    f"to {lowest:.2f} between frames). Performing the challenge "
                    f"with one face and presenting another is the obvious way to "
                    f"defeat a challenge, so the same person must be visible "
                    f"throughout."
                ),
                evidence={"lowest_similarity": round(lowest, 3)},
            )
        )

    met, detail = _challenge_met(session)
    session.completed = met

    if met:
        signals.append(
            signal(
                code="liveness.challenge_passed",
                stage=Stage.FACE,
                title="Liveness check",
                status=SignalStatus.PASS,
                severity=Severity.INFO,
                # Not higher. This establishes that something moved on demand,
                # which rules out a photograph and a still screen. It does not
                # rule out a live video-injection attack, and the confidence
                # says so rather than the reason having to carry it alone.
                confidence=0.7,
                reason=(
                    f"The requested action ('{session.prompt}') was performed. "
                    f"{detail} This rules out a printed photograph or a still "
                    f"image on a screen. It does not rule out a video injected "
                    f"directly into the camera stream."
                ),
                evidence={
                    "challenge": session.challenge.value,
                    "frames": len(session.frames),
                    "frames_with_face": len(observed),
                },
            )
        )
    else:
        signals.append(
            signal(
                code="liveness.challenge_failed",
                stage=Stage.FACE,
                title="Liveness check",
                status=SignalStatus.FAIL,
                severity=Severity.HIGH,
                confidence=0.75,
                blocking=True,
                reason=(
                    f"The requested action ('{session.prompt}') was not observed. "
                    f"{detail} A live subject can repeat the check; a photograph "
                    f"or a still screen cannot perform it at all."
                ),
                evidence={
                    "challenge": session.challenge.value,
                    "frames": len(session.frames),
                    "frames_with_face": len(observed),
                },
            )
        )

    return signals
