"""
The shared state store.

Every behaviour is pinned for BOTH backends through one parametrised fixture,
because the point of the abstraction is that swapping memory for Redis changes
where state lives and nothing else. A behaviour that held for one backend only
would surface in production -- the first time more than one worker ran.
"""

from __future__ import annotations

import json

import fakeredis
import numpy as np
import pytest

from app.core.state import (
    MemoryStateStore,
    RedisStateStore,
    StateUnavailable,
    build_state_store,
)
from app.pipeline.stages.liveness import (
    Challenge,
    FrameObservation,
    frame_from_dict,
    frame_to_dict,
    session_from_parts,
    session_header,
    start_session,
)


def redis_store(server: fakeredis.FakeServer | None = None) -> RedisStateStore:
    return RedisStateStore(client=fakeredis.FakeRedis(server=server or fakeredis.FakeServer()))


@pytest.fixture(params=["memory", "redis"])
def store(request):
    return MemoryStateStore() if request.param == "memory" else redis_store()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ------------------------------------------------------------ both backends --


class TestEitherBackend:
    def test_a_revocation_is_remembered(self, store):
        assert not store.is_revoked("sid:a")
        store.revoke("sid:a", 60)
        assert store.is_revoked("sid:a")
        assert not store.is_revoked("sid:b")

    def test_a_rate_limit_admits_the_allowance_then_refuses(self, store):
        results = [store.hit("login:ip:1", 3, 60) for _ in range(4)]
        assert results[:3] == [0, 0, 0]
        assert results[3] >= 1  # seconds to wait

    def test_rate_limit_buckets_do_not_share_an_allowance(self, store):
        for _ in range(3):
            store.hit("login:ip:1", 3, 60)
        assert store.hit("login:ip:2", 3, 60) == 0

    def test_a_liveness_session_round_trips(self, store):
        store.liveness_create("s1", {"challenge": "blink"}, 60)
        assert store.liveness_append("s1", {"index": 0}, 60) == 1
        assert store.liveness_append("s1", {"index": 1}, 60) == 2
        header, frames = store.liveness_load("s1")
        assert header == {"challenge": "blink"}
        assert [f["index"] for f in frames] == [0, 1]
        store.liveness_delete("s1")
        assert store.liveness_load("s1") is None

    def test_frames_cannot_be_added_to_a_session_that_does_not_exist(self, store):
        assert store.liveness_append("never-opened", {"index": 0}, 60) is None

    def test_reset_clears_everything(self, store):
        store.revoke("sid:a", 60)
        store.hit("k", 1, 60)
        store.liveness_create("s1", {"challenge": "blink"}, 60)
        store.reset()
        assert not store.is_revoked("sid:a")
        assert store.hit("k", 1, 60) == 0
        assert store.liveness_load("s1") is None


# ------------------------------------------------------------------ memory --


class TestMemoryExpiry:
    def test_a_revocation_lapses_after_its_ttl(self):
        clock = Clock()
        store = MemoryStateStore(clock=clock)
        store.revoke("sid:a", 10)
        clock.now += 11
        assert not store.is_revoked("sid:a")

    def test_the_rate_window_slides(self):
        clock = Clock()
        store = MemoryStateStore(clock=clock)
        for _ in range(3):
            store.hit("k", 3, 60)
        assert store.hit("k", 3, 60) >= 1
        clock.now += 61
        assert store.hit("k", 3, 60) == 0

    def test_a_liveness_session_expires(self):
        clock = Clock()
        store = MemoryStateStore(clock=clock)
        store.liveness_create("s1", {"challenge": "blink"}, 30)
        clock.now += 31
        assert store.liveness_load("s1") is None
        assert store.liveness_append("s1", {"index": 0}, 30) is None


# ------------------------------------------------------------------- redis --


class TestRedis:
    def test_two_processes_on_one_redis_see_the_same_state(self):
        """
        What Redis is for. Two stores on one server stand in for two workers:
        a sign-out, a rate-limit hit or a liveness frame recorded through one
        must be visible through the other.
        """
        server = fakeredis.FakeServer()
        worker_a, worker_b = redis_store(server), redis_store(server)

        worker_a.revoke("sid:s", 60)
        assert worker_b.is_revoked("sid:s")

        worker_a.hit("login:ip:1", 3, 60)
        worker_b.hit("login:ip:1", 3, 60)
        assert worker_a.hit("login:ip:1", 3, 60) == 0
        # The fourth request is refused wherever it lands -- one allowance,
        # not one per worker.
        assert worker_b.hit("login:ip:1", 3, 60) >= 1

        worker_a.liveness_create("s1", {"challenge": "turn_left"}, 60)
        worker_b.liveness_append("s1", {"index": 0}, 60)
        assert worker_a.liveness_load("s1") == ({"challenge": "turn_left"}, [{"index": 0}])

    def test_keys_expire_on_their_own(self):
        """Nothing written here should outlive its purpose and pile up."""
        client = fakeredis.FakeRedis()
        store = RedisStateStore(client=client)
        store.revoke("sid:s", 90)
        store.hit("k", 5, 60)
        store.liveness_create("s1", {"challenge": "blink"}, 180)
        store.liveness_append("s1", {"index": 0}, 180)
        assert 0 < client.ttl("vs:revoked:sid:s") <= 90
        assert 0 < client.ttl("vs:rl:k") <= 61
        assert 0 < client.ttl("vs:lv:s1:h") <= 180
        assert 0 < client.ttl("vs:lv:s1:f") <= 180

    def test_an_unreachable_redis_raises_rather_than_guessing(self):
        server = fakeredis.FakeServer()
        server.connected = False
        store = redis_store(server)
        with pytest.raises(StateUnavailable):
            store.is_revoked("sid:s")
        with pytest.raises(StateUnavailable):
            store.hit("k", 1, 60)
        with pytest.raises(StateUnavailable):
            store.liveness_load("s1")
        assert store.ping() is False

    def test_stored_values_are_json_not_pickle(self):
        client = fakeredis.FakeRedis()
        store = RedisStateStore(client=client)
        store.liveness_create("s1", {"challenge": "blink"}, 60)
        store.liveness_append("s1", {"index": 0}, 60)
        assert json.loads(client.get("vs:lv:s1:h")) == {"challenge": "blink"}
        assert json.loads(client.lindex("vs:lv:s1:f", 0)) == {"index": 0}

    def test_only_this_services_keys_are_reset(self):
        client = fakeredis.FakeRedis()
        client.set("someone-elses-key", b"1")
        store = RedisStateStore(client=client)
        store.revoke("sid:s", 60)
        store.reset()
        assert client.get("someone-elses-key") == b"1"
        assert not store.is_revoked("sid:s")


# --------------------------------------------------------------- selection --


class TestSelection:
    def test_memory_when_asked(self):
        assert build_state_store("memory").kind == "memory"

    def test_redis_when_asked_without_connecting(self):
        # Nothing listens on port 1; building must not try to connect.
        assert build_state_store("redis", url="redis://localhost:1/0").kind == "redis"

    def test_a_misspelt_backend_is_refused(self):
        with pytest.raises(ValueError, match="VERISHIELD_STATE_BACKEND"):
            build_state_store("rediss")


# ------------------------------------------------ liveness session storage --


class TestLivenessSerialisation:
    def test_a_frame_survives_the_trip_through_json(self):
        embedding = np.random.default_rng(0).standard_normal(512).astype(np.float32)
        frame = FrameObservation(
            index=np.int64(3),
            face_found=np.bool_(True),
            eye_openness=np.float32(0.31),
            yaw=np.float64(-12.5),
            pitch=None,
            embedding=embedding,
        )
        # Must be plain JSON: numpy scalars would make json.dumps raise.
        back = frame_from_dict(json.loads(json.dumps(frame_to_dict(frame))))
        assert (back.index, back.face_found, back.pitch) == (3, True, None)
        assert back.eye_openness == pytest.approx(0.31, abs=1e-6)
        assert back.yaw == -12.5
        assert np.array_equal(back.embedding, embedding)

    def test_a_session_rebuilds_with_its_challenge_and_start_time(self):
        session = start_session(Challenge.LOOK_UP)
        header = json.loads(json.dumps(session_header(session)))
        rebuilt = session_from_parts(session.session_id, header, [])
        assert rebuilt.challenge is Challenge.LOOK_UP
        assert rebuilt.created_at == session.created_at
        assert not rebuilt.expired
