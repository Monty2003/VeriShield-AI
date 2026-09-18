"""
Shared server state.

Three things this service keeps between requests, and must not keep in one
process's memory once more than one worker serves traffic:

  * revoked sign-ins  -- or a session signed out on one worker is still valid
                         on the next
  * rate-limit hits   -- or N workers quietly multiply every allowance by N
  * liveness sessions -- or frames posted to worker B cannot find a session
                         that was opened on worker A

Two backends behind one interface. MemoryStateStore is the previous behaviour,
kept for local development and single-worker runs. RedisStateStore shares the
state through Redis and is what a multi-worker deployment needs. Which one runs
is chosen by VERISHIELD_STATE_BACKEND (see app/core/config.py).

What to do when Redis fails is the caller's decision, not this module's. A
Redis error surfaces as StateUnavailable; the auth layer then refuses the
request, because "was this session signed out?" must not be answered with a
guess, while rate limiting lets the request through, because logins are still
protected by account lockout in the user store.

Values are stored as JSON, never pickled. Data read back from a shared store is
only as trustworthy as everything else that can write to it, and unpickling
would hand that trust the ability to execute code.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Protocol

from app.core.config import settings

# Namespaces every key, so this service can share a Redis instance safely.
PREFIX = "vs:"

# Per-operation Redis timeout. A hosted Redis is a network hop away, like Atlas,
# but an operation is a single round trip on an established connection.
REDIS_TIMEOUT_SECONDS = 3.0

Frame = dict[str, Any]
Header = dict[str, Any]


class StateUnavailable(RuntimeError):
    """The shared store could not be reached, or answered with an error."""


class StateStore(Protocol):
    kind: str

    def describe(self) -> str: ...
    def ping(self) -> bool: ...
    def revoke(self, key: str, ttl_seconds: int) -> None: ...
    def is_revoked(self, key: str) -> bool: ...
    def hit(self, key: str, allowance: int, window_seconds: int) -> int: ...
    def liveness_create(self, session_id: str, header: Header, ttl_seconds: int) -> None: ...
    def liveness_append(self, session_id: str, frame: Frame, ttl_seconds: int) -> int | None: ...
    def liveness_load(self, session_id: str) -> tuple[Header, list[Frame]] | None: ...
    def liveness_delete(self, session_id: str) -> None: ...
    def reset(self) -> None: ...


# ---------------------------------------------------------------- memory --


class MemoryStateStore:
    """
    This process only. Correct for one worker; lost on restart.

    FastAPI runs synchronous endpoints on a thread pool, so every access is
    under a lock even though there is only one process.
    """

    kind = "memory"

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._revoked: dict[str, float] = {}
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._liveness: dict[str, tuple[float, Header, list[Frame]]] = {}

    def describe(self) -> str:
        return "in-process (one worker only; lost on restart)"

    def ping(self) -> bool:
        return True

    def revoke(self, key: str, ttl_seconds: int) -> None:
        with self._lock:
            now = self._clock()
            if len(self._revoked) > 1000:
                self._revoked = {k: t for k, t in self._revoked.items() if t > now}
            self._revoked[key] = now + max(1, ttl_seconds)

    def is_revoked(self, key: str) -> bool:
        with self._lock:
            until = self._revoked.get(key)
            if until is None:
                return False
            if until <= self._clock():
                del self._revoked[key]
                return False
            return True

    def hit(self, key: str, allowance: int, window_seconds: int) -> int:
        """0 if this request fits the allowance, else seconds until one would."""
        with self._lock:
            now = self._clock()
            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= allowance:
                return int(window_seconds - (now - hits[0])) + 1
            hits.append(now)
            return 0

    def liveness_create(self, session_id: str, header: Header, ttl_seconds: int) -> None:
        with self._lock:
            self._liveness[session_id] = (self._clock() + ttl_seconds, dict(header), [])

    def _live(self, session_id: str) -> tuple[float, Header, list[Frame]] | None:
        entry = self._liveness.get(session_id)
        if entry is None or entry[0] <= self._clock():
            self._liveness.pop(session_id, None)
            return None
        return entry

    def liveness_append(self, session_id: str, frame: Frame, ttl_seconds: int) -> int | None:
        with self._lock:
            entry = self._live(session_id)
            if entry is None:
                return None
            entry[2].append(dict(frame))
            return len(entry[2])

    def liveness_load(self, session_id: str) -> tuple[Header, list[Frame]] | None:
        with self._lock:
            entry = self._live(session_id)
            if entry is None:
                return None
            return dict(entry[1]), [dict(f) for f in entry[2]]

    def liveness_delete(self, session_id: str) -> None:
        with self._lock:
            self._liveness.pop(session_id, None)

    def reset(self) -> None:
        with self._lock:
            self._revoked.clear()
            self._hits.clear()
            self._liveness.clear()


# ----------------------------------------------------------------- redis --


class RedisStateStore:
    """Shared through Redis, so every worker and instance sees the same state."""

    kind = "redis"

    def __init__(self, url: str | None = None, client: Any = None) -> None:
        if client is None:
            import redis

            client = redis.Redis.from_url(
                url or settings.redis_url,
                socket_timeout=REDIS_TIMEOUT_SECONDS,
                socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            )
        self._r = client

    def _call(self, operation: Callable[[], Any]) -> Any:
        import redis

        try:
            return operation()
        except redis.RedisError as exc:
            raise StateUnavailable(f"{type(exc).__name__}: {exc}") from exc

    def describe(self) -> str:
        return "redis (shared across workers)"

    def ping(self) -> bool:
        try:
            return bool(self._call(self._r.ping))
        except StateUnavailable:
            return False

    # -- revocation --

    def revoke(self, key: str, ttl_seconds: int) -> None:
        # Expires on its own once nothing it revoked could still be presented.
        self._call(lambda: self._r.set(f"{PREFIX}revoked:{key}", b"1", ex=max(1, ttl_seconds)))

    def is_revoked(self, key: str) -> bool:
        return bool(self._call(lambda: self._r.exists(f"{PREFIX}revoked:{key}")))

    # -- rate limiting --

    def hit(self, key: str, allowance: int, window_seconds: int) -> int:
        """
        Sliding window on a sorted set, one member per request.

        The request is added first and counted second, inside one MULTI, so two
        workers racing at the limit cannot both slip under it: the later one
        sees itself over the allowance and removes its own entry.
        """
        name = f"{PREFIX}rl:{key}"
        now = time.time()
        member = f"{now:.6f}:{secrets.token_hex(4)}"

        def run() -> list[Any]:
            pipe = self._r.pipeline(transaction=True)
            pipe.zremrangebyscore(name, "-inf", now - window_seconds)
            pipe.zadd(name, {member: now})
            pipe.zcard(name)
            pipe.zrange(name, 0, 0, withscores=True)
            pipe.expire(name, window_seconds + 1)
            return pipe.execute()

        _, _, count, oldest, _ = self._call(run)
        if int(count) <= allowance:
            return 0
        self._call(lambda: self._r.zrem(name, member))
        first = float(oldest[0][1]) if oldest else now
        return int(window_seconds - (now - first)) + 1

    # -- liveness sessions --

    @staticmethod
    def _keys(session_id: str) -> tuple[str, str]:
        return f"{PREFIX}lv:{session_id}:h", f"{PREFIX}lv:{session_id}:f"

    def liveness_create(self, session_id: str, header: Header, ttl_seconds: int) -> None:
        head, frames = self._keys(session_id)

        def run() -> list[Any]:
            pipe = self._r.pipeline(transaction=True)
            pipe.delete(frames)
            pipe.set(head, json.dumps(header), ex=ttl_seconds)
            return pipe.execute()

        self._call(run)

    def liveness_append(self, session_id: str, frame: Frame, ttl_seconds: int) -> int | None:
        head, frames = self._keys(session_id)
        if not self._call(lambda: self._r.exists(head)):
            return None

        def run() -> list[Any]:
            # RPUSH is atomic, so concurrent frames are never lost to a
            # read-modify-write race on a shared list.
            pipe = self._r.pipeline(transaction=True)
            pipe.rpush(frames, json.dumps(frame))
            pipe.expire(frames, ttl_seconds)
            return pipe.execute()

        count, _ = self._call(run)
        return int(count)

    def liveness_load(self, session_id: str) -> tuple[Header, list[Frame]] | None:
        head, frames = self._keys(session_id)

        def run() -> list[Any]:
            pipe = self._r.pipeline(transaction=False)
            pipe.get(head)
            pipe.lrange(frames, 0, -1)
            return pipe.execute()

        header, items = self._call(run)
        if header is None:
            return None
        return json.loads(header), [json.loads(item) for item in items]

    def liveness_delete(self, session_id: str) -> None:
        self._call(lambda: self._r.delete(*self._keys(session_id)))

    def reset(self) -> None:
        """Remove this service's keys only. For tests."""
        keys = list(self._call(lambda: list(self._r.scan_iter(f"{PREFIX}*"))))
        if keys:
            self._call(lambda: self._r.delete(*keys))


# --------------------------------------------------------------- selection --

BACKENDS = ("memory", "redis")


def build_state_store(backend: str | None = None, url: str | None = None) -> StateStore:
    """The store VERISHIELD_STATE_BACKEND asks for. Never connects eagerly."""
    choice = (backend or settings.state_backend).strip().lower()
    if choice == "memory":
        return MemoryStateStore()
    if choice == "redis":
        return RedisStateStore(url=url)
    raise ValueError(
        f"VERISHIELD_STATE_BACKEND must be one of {BACKENDS}, not {choice!r}."
    )


_current: StateStore | None = None


def store() -> StateStore:
    """
    The process-wide store.

    Callers go through this function rather than importing an instance, so
    replacing the store -- in tests, or at startup -- reaches every module.
    Importing the object by name binds it once and would not.
    """
    global _current
    if _current is None:
        _current = build_state_store()
    return _current


def use(replacement: StateStore) -> StateStore | None:
    """Swap the process-wide store. Returns the previous one."""
    global _current
    previous, _current = _current, replacement
    return previous
