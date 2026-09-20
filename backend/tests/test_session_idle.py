"""
A sign-in that goes unused is ended by the server, not only by the page.

A countdown that lives in the browser is a display: anyone holding the token
could keep calling the API after the page had "signed out". These pin the
server's side of it -- what renews the clock, what does not, and that an idle
sign-in is ended outright, refresh token included.
"""

from __future__ import annotations

import pytest

from app.core import state
from app.core.config import settings
from tests.test_security import PASSWORD, _bearer, _login, api, signing_key  # noqa: F401

IDLE = 5 * 60


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(settings, "session_idle_minutes", 5)
    tick = Clock()
    previous = state.use(state.MemoryStateStore(clock=tick))
    yield tick
    state.use(previous)


def me(api, token):  # noqa: F811
    return api.get("/api/v1/auth/me", headers=_bearer(token))


class TestIdleTimeout:
    def test_a_sign_in_that_goes_unused_is_ended(self, api, clock):  # noqa: F811
        access, _ = _login(api)
        clock.advance(IDLE + 1)
        response = me(api, access)
        assert response.status_code == 401
        assert "without activity" in response.json()["detail"]

    def test_its_refresh_token_dies_with_it(self, api, clock):  # noqa: F811
        access, refresh = _login(api)
        clock.advance(IDLE + 1)
        me(api, access)  # discovers the idle session and ends it
        response = api.post("/api/v1/auth/refresh", params={"refresh_token": refresh})
        assert response.status_code == 401

    def test_using_the_session_keeps_it(self, api, clock):  # noqa: F811
        access, _ = _login(api)
        for _ in range(3):  # 12 minutes in all, never 5 without a request
            clock.advance(IDLE - 60)
            assert me(api, access).status_code == 200

    def test_the_keepalive_renews_it(self, api, clock):  # noqa: F811
        access, _ = _login(api)
        clock.advance(IDLE - 30)
        assert api.post("/api/v1/auth/session/keepalive", headers=_bearer(access)).status_code == 200
        clock.advance(IDLE - 30)
        assert me(api, access).status_code == 200

    def test_refreshing_is_not_activity(self, api, clock):  # noqa: F811
        # A page left open refreshes on its own; that must not keep it alive.
        access, refresh = _login(api)
        clock.advance(IDLE - 60)
        assert api.post("/api/v1/auth/refresh", params={"refresh_token": refresh}).status_code == 200
        clock.advance(120)
        assert me(api, access).status_code == 401

    def test_an_idle_refresh_is_refused_even_without_a_request_first(self, api, clock):  # noqa: F811
        _, refresh = _login(api)
        clock.advance(IDLE + 1)
        response = api.post("/api/v1/auth/refresh", params={"refresh_token": refresh})
        assert response.status_code == 401
        assert "without activity" in response.json()["detail"]

    def test_other_sign_ins_keep_their_own_clocks(self, api, clock):  # noqa: F811
        idle, _ = _login(api, "op")
        busy, _ = _login(api, "rev")
        clock.advance(IDLE - 60)
        me(api, busy)
        clock.advance(120)
        assert me(api, idle).status_code == 401
        assert me(api, busy).status_code == 200

    def test_zero_turns_it_off(self, api, clock, monkeypatch):  # noqa: F811
        monkeypatch.setattr(settings, "session_idle_minutes", 0)
        access, _ = _login(api)
        clock.advance(24 * 3600)
        assert me(api, access).status_code == 200


class TestSessionInfo:
    def test_the_page_learns_the_limit_from_the_server(self, api, clock):  # noqa: F811
        access, _ = _login(api)
        info = api.get("/api/v1/auth/session", headers=_bearer(access)).json()
        assert info["idle_timeout_seconds"] == IDLE
        assert info["username"] == "op"
        assert "client_ip" in info


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_a_mark_lasts_exactly_its_time(kind):
    import fakeredis

    tick = Clock()
    store = (
        state.MemoryStateStore(clock=tick)
        if kind == "memory"
        else state.RedisStateStore(client=fakeredis.FakeRedis())
    )
    store.mark("alive:x", 60)
    assert store.is_marked("alive:x")
    assert not store.is_marked("alive:y")
    assert not store.is_revoked("alive:x")  # a separate namespace from revocations
    if kind == "memory":
        tick.advance(61)
        assert not store.is_marked("alive:x")
