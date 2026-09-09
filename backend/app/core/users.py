"""
User accounts.

Backed by MongoDB when it is reachable. Unlike every other store in this
project, this one does NOT fall back to something permissive when the database
is down -- `available` goes false and authentication refuses.

That asymmetry is the point. Losing the audit store costs a record of a
decision; losing the user store, if it failed open, would cost the decision
about who is allowed to make one. A verification service that keeps working
without its database is resilient. An authorisation layer that keeps working
without its database is not authorising anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.core.security import Role, hash_password, needs_rehash, verify_password

# Failed attempts before an account is locked, and for how long.
#
# Locking is per ACCOUNT rather than per IP: an attacker spreading attempts
# across addresses is the normal case, and an IP-based lock mostly succeeds at
# locking out offices behind one NAT.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_MINUTES = 15


@dataclass
class User:
    username: str
    password_hash: str
    role: Role
    full_name: str = ""
    disabled: bool = False
    failed_attempts: int = 0
    locked_until: datetime | None = None
    created_at: datetime | None = None

    @property
    def locked(self) -> bool:
        if self.locked_until is None:
            return False
        return datetime.now(timezone.utc) < self.locked_until

    def public(self) -> dict[str, object]:
        """Everything safe to return to a client. Never the hash."""
        return {
            "username": self.username,
            "role": self.role.value,
            "full_name": self.full_name,
            "disabled": self.disabled,
            "locked": self.locked,
        }


class UserStore:
    """MongoDB-backed accounts, with no permissive fallback."""

    COLLECTION = "users"

    def __init__(self, url: str | None = None, database: str | None = None) -> None:
        self.url = url or settings.mongo_url
        self.database = database or settings.mongo_db
        self._client = None
        self._error = ""
        self._probed_at = 0.0

    # ---- connection -----------------------------------------------------

    @property
    def available(self) -> bool:
        return self._connect() is not None

    @property
    def error(self) -> str:
        self._connect()
        return self._error

    def _connect(self):
        if self._client is not None:
            return self._client

        import time

        # Cached the same way the audit store caches, so a health check that
        # polls does not open a socket per request to a database that is down.
        if self._error and (time.monotonic() - self._probed_at) < 10.0:
            return None
        self._probed_at = time.monotonic()

        try:
            from pymongo import MongoClient

            timeout = settings.mongo_timeout_ms
            client = MongoClient(
                self.url,
                serverSelectionTimeoutMS=timeout,
                connectTimeoutMS=timeout,
                socketTimeoutMS=timeout,
            )
            client.admin.command("ping")
            client[self.database][self.COLLECTION].create_index("username", unique=True)
            self._client = client
            self._error = ""
        except Exception as exc:  # noqa: BLE001
            self._error = f"{type(exc).__name__}: {exc}"
            return None
        return self._client

    def _collection(self):
        client = self._connect()
        return None if client is None else client[self.database][self.COLLECTION]

    # ---- reads ----------------------------------------------------------

    def get(self, username: str) -> User | None:
        collection = self._collection()
        if collection is None:
            return None
        record = collection.find_one({"username": username.lower()})
        return self._from_record(record) if record else None

    def list_users(self) -> list[User]:
        collection = self._collection()
        if collection is None:
            return []
        return [self._from_record(r) for r in collection.find({})]

    @staticmethod
    def _from_record(record: dict) -> User:
        locked_until = record.get("locked_until")
        if isinstance(locked_until, datetime) and locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        return User(
            username=record["username"],
            password_hash=record["password_hash"],
            role=Role(record.get("role", Role.OPERATOR.value)),
            full_name=record.get("full_name", ""),
            disabled=bool(record.get("disabled", False)),
            failed_attempts=int(record.get("failed_attempts", 0)),
            locked_until=locked_until,
            created_at=record.get("created_at"),
        )

    # ---- writes ---------------------------------------------------------

    def create(
        self, username: str, password: str, role: Role, full_name: str = ""
    ) -> User | None:
        collection = self._collection()
        if collection is None:
            return None

        user = User(
            username=username.lower(),
            password_hash=hash_password(password),
            role=role,
            full_name=full_name,
            created_at=datetime.now(timezone.utc),
        )
        collection.insert_one(
            {
                "username": user.username,
                "password_hash": user.password_hash,
                "role": user.role.value,
                "full_name": user.full_name,
                "disabled": False,
                "failed_attempts": 0,
                "locked_until": None,
                "created_at": user.created_at,
            }
        )
        return user

    def set_password(self, username: str, password: str) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        result = collection.update_one(
            {"username": username.lower()},
            {
                "$set": {
                    "password_hash": hash_password(password),
                    "failed_attempts": 0,
                    "locked_until": None,
                }
            },
        )
        return result.matched_count > 0

    def set_disabled(self, username: str, disabled: bool) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        result = collection.update_one(
            {"username": username.lower()}, {"$set": {"disabled": disabled}}
        )
        return result.matched_count > 0

    # ---- authentication -------------------------------------------------

    def authenticate(self, username: str, password: str) -> tuple[User | None, str]:
        """
        Verify credentials.

        Returns (user, reason). The reason is for LOGGING, not for the client:
        telling a caller whether the username exists hands an attacker a way to
        enumerate accounts, so the endpoint above collapses every failure into
        one message.
        """
        if not self.available:
            return None, "user store unavailable"

        user = self.get(username)
        if user is None:
            # Hash anyway. Returning immediately for an unknown username makes
            # that case measurably faster than a wrong password, which is
            # enough to enumerate valid accounts by timing.
            verify_password(password, hash_password("timing-equalisation"))
            return None, "no such user"

        if user.disabled:
            return None, "account disabled"
        if user.locked:
            return None, "account locked"

        if not verify_password(password, user.password_hash):
            self._record_failure(user)
            return None, "wrong password"

        # Argon2 parameters change over time; rehash on a successful login so
        # stored hashes keep up without asking anyone to change their password.
        if needs_rehash(user.password_hash):
            self.set_password(user.username, password)

        self._clear_failures(user)
        return user, "ok"

    def _record_failure(self, user: User) -> None:
        collection = self._collection()
        if collection is None:
            return

        attempts = user.failed_attempts + 1
        update: dict[str, object] = {"failed_attempts": attempts}
        if attempts >= MAX_FAILED_ATTEMPTS:
            from datetime import timedelta

            update["locked_until"] = datetime.now(timezone.utc) + timedelta(
                minutes=LOCKOUT_MINUTES
            )
            update["failed_attempts"] = 0
        collection.update_one({"username": user.username}, {"$set": update})

    def _clear_failures(self, user: User) -> None:
        collection = self._collection()
        if collection is None or (user.failed_attempts == 0 and not user.locked_until):
            return
        collection.update_one(
            {"username": user.username},
            {"$set": {"failed_attempts": 0, "locked_until": None}},
        )


user_store = UserStore()
