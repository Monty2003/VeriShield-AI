"""
Liveness tests.

These drive the decision logic with known observations rather than video, so
they run in milliseconds and need no camera. That split is deliberate: the
DECISION is what these lock down, and it is the part that can be stated
exactly -- eyes that close and reopen are a blink; a pose that never moves is
not a turn.

What they cannot establish is the spoof-detection RATE against real
presentation attacks. That needs recordings of people holding up photographs
and screens, which this project does not have, and no synthetic substitute
would measure it honestly.
"""

from __future__ import annotations

import pytest

from app.pipeline.stages.liveness import (
    BLINK_CLOSED_RATIO,
    IDENTITY_CONSISTENCY_THRESHOLD,
    TURN_DEGREES,
    Challenge,
    FrameObservation,
    evaluate,
    eye_openness,
    start_session,
)
from app.schemas.signals import SignalStatus


def session_with(challenge: Challenge, **series):
    """Build a session from known per-frame observations."""
    session = start_session(challenge)
    length = len(next(iter(series.values())))
    for index in range(length):
        session.frames.append(
            FrameObservation(
                index=index,
                face_found=True,
                eye_openness=series.get("openness", [None] * length)[index],
                yaw=series.get("yaw", [None] * length)[index],
                pitch=series.get("pitch", [None] * length)[index],
                embedding=series.get("embedding", [None] * length)[index],
            )
        )
    return session


def verdict(session):
    return next(
        s
        for s in evaluate(session)
        if s.code.startswith("liveness.challenge") or s.code == "liveness.identity_changed"
    )


class TestBlink:
    def test_a_real_blink_passes(self):
        """Open, closed, open again -- the shape of an actual blink."""
        result = verdict(
            session_with(Challenge.BLINK, openness=[0.36, 0.35, 0.12, 0.11, 0.34, 0.36])
        )
        assert result.status == SignalStatus.PASS

    def test_a_static_photograph_fails(self):
        """
        The attack this exists to stop. A printed photograph held to the camera
        produces an unchanging eye aperture.
        """
        result = verdict(session_with(Challenge.BLINK, openness=[0.36] * 8))
        assert result.status == SignalStatus.FAIL
        assert result.blocking is True

    def test_a_photograph_of_closed_eyes_fails(self):
        """
        Closure alone is not a blink. Without requiring the eyes to REOPEN, a
        photograph of someone mid-blink would satisfy the check.
        """
        result = verdict(session_with(Challenge.BLINK, openness=[0.11] * 8))
        assert result.status == SignalStatus.FAIL

    def test_threshold_is_relative_to_the_subject(self):
        """
        Eye shape varies enormously between people. An absolute threshold would
        call some people permanently blinking and others incapable of it, so
        closure is measured against the subject's own open eye.
        """
        # Narrow eyes throughout, with a proportional blink.
        narrow = verdict(
            session_with(Challenge.BLINK, openness=[0.20, 0.20, 0.06, 0.19, 0.20])
        )
        # Wide eyes, same proportional blink.
        wide = verdict(
            session_with(Challenge.BLINK, openness=[0.50, 0.50, 0.15, 0.48, 0.50])
        )
        assert narrow.status == SignalStatus.PASS
        assert wide.status == SignalStatus.PASS

    def test_ratio_constant_is_below_one(self):
        assert 0 < BLINK_CLOSED_RATIO < 1


class TestHeadTurn:
    def test_a_real_turn_passes(self):
        result = verdict(
            session_with(Challenge.TURN_LEFT, yaw=[2.0, 8.0, 18.0, 27.0, 20.0])
        )
        assert result.status == SignalStatus.PASS

    def test_a_flat_image_fails(self):
        """A photograph held up does not change pose, however it is waved about."""
        result = verdict(session_with(Challenge.TURN_LEFT, yaw=[3.0] * 8))
        assert result.status == SignalStatus.FAIL
        assert result.blocking is True

    def test_small_wobble_is_not_a_turn(self):
        result = verdict(
            session_with(Challenge.TURN_LEFT, yaw=[2.0, 4.0, 3.0, 5.0, 2.0])
        )
        assert result.status == SignalStatus.FAIL

    def test_look_up_uses_pitch(self):
        result = verdict(
            session_with(Challenge.LOOK_UP, pitch=[-5.0, 2.0, 10.0, 16.0, 8.0])
        )
        assert result.status == SignalStatus.PASS

    def test_turn_threshold_is_a_real_rotation(self):
        assert TURN_DEGREES >= 10.0


class TestIdentityConsistency:
    """
    The obvious way to defeat a challenge protocol: perform the action with
    your own face, then present the victim's photograph for the frame that
    gets matched against their document.
    """

    def test_face_swapped_mid_challenge_is_caught(self):
        import numpy as np

        me = np.array([1.0, 0.0, 0.0, 0.0])
        someone_else = np.array([0.0, 1.0, 0.0, 0.0])
        session = session_with(
            Challenge.BLINK,
            openness=[0.36, 0.12, 0.36, 0.36],
            embedding=[me, me, someone_else, someone_else],
        )
        signals = evaluate(session)
        assert any(s.code == "liveness.identity_changed" and s.blocking for s in signals)

    def test_same_face_throughout_is_accepted(self):
        import numpy as np

        me = np.array([1.0, 0.0, 0.0, 0.0])
        session = session_with(
            Challenge.BLINK, openness=[0.36, 0.12, 0.36], embedding=[me, me, me]
        )
        assert not any(s.code == "liveness.identity_changed" for s in evaluate(session))

    def test_threshold_is_set(self):
        assert 0 < IDENTITY_CONSISTENCY_THRESHOLD < 1


class TestSessionIntegrity:
    def test_challenge_is_chosen_by_the_server(self):
        """
        Unpredictability is the whole security property. A recording made in
        advance cannot contain the right action unless the attacker knew which
        one would be asked for.
        """
        chosen = {start_session().challenge for _ in range(60)}
        assert len(chosen) > 1

    def test_no_frames_with_a_face_fails(self):
        session = start_session(Challenge.BLINK)
        session.frames.append(FrameObservation(index=0, face_found=False))
        signals = evaluate(session)
        assert signals[0].code == "liveness.no_face"
        assert signals[0].blocking is True

    def test_too_few_frames_fails(self):
        result = verdict(session_with(Challenge.BLINK, openness=[0.36, 0.12]))
        assert result.status == SignalStatus.FAIL


class TestEyeOpennessMeasure:
    def test_closed_eye_scores_lower_than_open(self):
        import numpy as np

        landmarks = np.zeros((106, 2))
        # Left eye: indices 33-42, right eye: 87-96, found by measuring against
        # the detector's own eye keypoints rather than taken from docs.
        for indices in (range(33, 43), range(87, 97)):
            for offset, i in enumerate(indices):
                landmarks[i] = [offset, offset % 2 * 10]  # tall spread = open
        wide = eye_openness(landmarks)

        for indices in (range(33, 43), range(87, 97)):
            for offset, i in enumerate(indices):
                landmarks[i] = [offset, offset % 2 * 1]  # flat spread = closed
        narrow = eye_openness(landmarks)

        assert narrow < wide


class TestHonesty:
    def test_a_pass_does_not_claim_more_than_it_shows(self):
        """
        Challenge-response rules out a photograph and a still screen. It does
        not rule out frames injected into the camera stream, and the reason
        must say so rather than implying the subject is proven present.
        """
        result = verdict(
            session_with(Challenge.BLINK, openness=[0.36, 0.35, 0.12, 0.34, 0.36])
        )
        assert result.confidence < 0.8
        assert "does not rule out" in result.reason.lower()
