"""
Passwords, tokens, and roles.

Two decisions here are deliberate departures from how the rest of this project
behaves, and both matter more than the code around them.

**Auth fails closed.** Everywhere else, a missing component degrades: if
MongoDB is unreachable the pipeline still verifies documents and says the audit
trail is unavailable. That is right for evidence -- losing a forensic check
costs information. It is exactly wrong for authorisation, where losing the
component means losing the boundary itself. If the user store cannot be
reached, requests are refused rather than admitted.

**There is no default secret.** A signing key with a working development
default is the single most reliable way to ship a production system that anyone
can forge tokens for: it works on a laptop, nobody notices it is still the
default, and the deployment is silently open. So the key is required, and a
server started without one refuses to issue or accept tokens at all.
"""

from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Argon2id: the current recommendation for password storage, and unlike bcrypt
# it has no silent 72-byte truncation to reason about.
_hasher = PasswordHasher()

ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 30

# Refresh tokens live longer but do far less: they can only mint access tokens,
# never authorise a request themselves. A stolen access token expires in
# minutes; a stolen refresh token is revocable because it carries an id the
# server can blacklist.
REFRESH_TOKEN_DAYS = 7

MIN_SECRET_LENGTH = 32


class Role(str, Enum):
    """
    Who may do what.

    These are not decorative. The boundary between OPERATOR and REVIEWER is a
    real privacy decision that already existed in this codebase: verification
    responses mask identity numbers by default, and only a reviewer has cause
    to unmask one. Roles that do not gate anything are worse than none, because
    they imply a control that is not there.
    """

    # Submits documents and reads the verdict, with identifiers masked.
    OPERATOR = "operator"
    # Everything an operator can do, plus the audit trail and the ability to
    # unmask an identity number when a decision has to be justified.
    REVIEWER = "reviewer"
    # Manages accounts.
    ADMIN = "admin"


# Capabilities each role holds. Written as an explicit table rather than a
# hierarchy check, so that reading one line answers "can a reviewer do X?"
ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.OPERATOR: {"verify:submit", "verify:read"},
    Role.REVIEWER: {
        "verify:submit",
        "verify:read",
        "verify:reveal_identifiers",
        "audit:read",
    },
    Role.ADMIN: {
        "verify:submit",
        "verify:read",
        "verify:reveal_identifiers",
        "audit:read",
        "users:manage",
    },
}


class SecretNotConfigured(RuntimeError):
    """Raised when token operations are attempted with no signing key."""


def _secret() -> str:
    """
    The JWT signing key, or an error.

    Read at call time rather than import time so a test can set it and so a
    misconfigured server fails on the first token operation with a clear
    message, rather than at import with a stack trace nobody reads.
    """
    from app.core.config import settings
    
    key = os.environ.get("VERISHIELD_JWT_SECRET") or settings.jwt_secret
    if len(key) < MIN_SECRET_LENGTH:
        raise SecretNotConfigured(
            "VERISHIELD_JWT_SECRET is not set, or is shorter than "
            f"{MIN_SECRET_LENGTH} characters. There is deliberately no default: "
            "a development key that works everywhere is a production system "
            "anyone can mint tokens for. Generate one with:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return key


def generate_secret() -> str:
    """A signing key suitable for VERISHIELD_JWT_SECRET."""
    return secrets.token_urlsafe(48)


# --- passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a password for storage. Never store or log the plaintext."""
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Check a password against its stored hash.

    Returns False for a malformed hash rather than raising, so that a corrupt
    record fails authentication instead of failing the request in a way that
    might be handled differently upstream.
    """
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether a stored hash was made with outdated parameters."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except Exception:  # noqa: BLE001
        return False


def password_problems(password: str) -> list[str]:
    """
    Reasons a password is unacceptable, empty if it is fine.

    Length is weighted over character-class rules on purpose: composition
    requirements push people toward predictable substitutions, while length is
    what actually costs an attacker.
    """
    problems: list[str] = []
    if len(password) < 12:
        problems.append("must be at least 12 characters")
    if password.lower() in {
        "password", "password123", "verishield", "administrator", "changeme",
    }:
        problems.append("is a commonly used password")
    if len(set(password)) < 5:
        problems.append("uses too few distinct characters")
    return problems


# --- tokens ----------------------------------------------------------------


def create_access_token(
    subject: str, role: Role, expires_minutes: int = ACCESS_TOKEN_MINUTES
) -> str:
    """Mint a short-lived access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role.value,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        # A unique id per token, so an individual one can be revoked without
        # invalidating every token the user holds.
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def create_refresh_token(subject: str) -> tuple[str, str]:
    """Mint a refresh token. Returns (token, jti) so the jti can be stored."""
    now = datetime.now(timezone.utc)
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_DAYS),
        "jti": jti,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM), jti


def decode_token(token: str, expected_type: str = "access") -> dict:
    """
    Verify and decode a token.

    Raises jwt exceptions on anything wrong -- expiry, a bad signature, the
    wrong token type. The type check matters: without it a refresh token, which
    lives for a week, would be accepted wherever an access token is, quietly
    turning a 30-minute credential into a 7-day one.
    """
    payload = jwt.decode(
        token,
        _secret(),
        algorithms=[ALGORITHM],
        options={"require": ["exp", "sub", "jti"]},
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"expected a {expected_type} token, got {payload.get('type')!r}"
        )
    return payload


def has_permission(role: Role, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def constant_time_equals(a: str, b: str) -> bool:
    """
    Compare two secrets without leaking their contents through timing.

    Used for API keys, where a plain == would return faster the earlier it
    finds a mismatched character.
    """
    return hmac.compare_digest(a.encode(), b.encode())
