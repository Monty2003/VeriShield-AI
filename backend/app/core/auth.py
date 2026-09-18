"""
Request-level authentication and authorisation.

Every protected endpoint depends on `current_user`, and every endpoint that
does something privileged depends on `requires(...)` naming the permission it
needs. Naming the permission rather than the role is what keeps the two from
drifting: adding a role means editing one table, not auditing every route for
`if role == ...`.
"""

from __future__ import annotations

import logging
import time

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.core import state
from app.core.security import (
    REFRESH_TOKEN_DAYS,
    Role,
    SecretNotConfigured,
    decode_token,
    has_permission,
)
from app.core.users import User, user_store

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# A signed-out session must stay revoked for as long as any token from it could
# still be presented -- the refresh token lives longest.
SESSION_REVOCATION_SECONDS = REFRESH_TOKEN_DAYS * 24 * 3600 + 60

_STORE_DOWN = (
    "The session store is unreachable, so this request cannot be authorised. "
    "Requests are refused rather than admitted when a sign-out cannot be "
    "checked."
)


def revoke_session(payload: dict) -> str:
    """
    End the sign-in a token belongs to. Returns what was revoked.

    The token's own id is revoked for the rest of its lifetime, and its session
    id for the lifetime of the longest token that session could hold. Tokens
    minted before sessions existed carry no sid; for those only the presented
    token can be revoked, and the caller is told so.
    """
    store = state.store()
    now = int(time.time())
    if payload.get("jti"):
        remaining = int(payload.get("exp", now)) - now
        store.revoke(f"jti:{payload['jti']}", max(1, remaining) + 60)
    if payload.get("sid"):
        store.revoke(f"sid:{payload['sid']}", SESSION_REVOCATION_SECONDS)
        return "session"
    return "token"


def is_revoked(payload: dict) -> bool:
    """Whether this token, or the sign-in it belongs to, has been ended."""
    store = state.store()
    if payload.get("jti") and store.is_revoked(f"jti:{payload['jti']}"):
        return True
    return bool(payload.get("sid")) and store.is_revoked(f"sid:{payload['sid']}")


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(token: str | None = Depends(oauth2_scheme)) -> User:
    """
    Resolve the caller, or refuse.

    Refuses on every uncertainty, including the user store being unreachable.
    Elsewhere in this project a missing component degrades gracefully; here it
    must not. A database outage that let unauthenticated requests through would
    turn an availability problem into an access-control one.
    """
    if not token:
        raise _unauthorised("Not authenticated.")

    try:
        payload = decode_token(token, expected_type="access")
    except SecretNotConfigured as exc:
        # A server with no signing key cannot verify anything. Saying so
        # plainly is better than pretending the token was merely invalid.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Authentication is not configured on this server. {exc}",
        ) from exc
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorised("Token expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorised("Invalid token.") from exc

    try:
        revoked = is_revoked(payload)
    except state.StateUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STORE_DOWN
        ) from exc
    if revoked:
        raise _unauthorised("This session has been signed out.")

    if not user_store.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The user store is unreachable, so this request cannot be "
                "authorised. Requests are refused rather than admitted when "
                "the authorisation boundary cannot be checked."
            ),
        )

    user = user_store.get(payload["sub"])
    if user is None:
        # The token is valid but the account is gone -- deleted since it was
        # issued. A signature alone is not authorisation.
        raise _unauthorised("Account no longer exists.")
    if user.disabled:
        raise _unauthorised("Account is disabled.")
    if user.locked:
        raise _unauthorised("Account is locked.")

    return user


def requires(permission: str):
    """
    Dependency factory gating an endpoint on a named permission.

        @router.get("/cases", dependencies=[Depends(requires("audit:read"))])
    """

    async def _check(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action needs the '{permission}' permission, which the "
                    f"'{user.role.value}' role does not hold."
                ),
            )
        return user

    return _check


async def optional_user(token: str | None = Depends(oauth2_scheme)) -> User | None:
    """The caller if authenticated, otherwise None. For endpoints open to both."""
    if not token:
        return None
    try:
        return await current_user(token)
    except HTTPException:
        return None


# --- rate limiting ---------------------------------------------------------

# Per-identity request budgets. Verification is far more expensive than a login
# -- OCR and face models run per document -- so the two are limited separately
# rather than sharing one allowance.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    # bucket: (requests, per_seconds)
    "login": (10, 300),
    "verify": (30, 60),
    "liveness": (120, 60),
    "default": (120, 60),
}

def _identity(request: Request, user: User | None) -> str:
    """
    Who to charge a request to.

    An authenticated user is charged by username, so one account cannot dodge
    its limit by changing address. Anonymous callers are charged by IP, which
    is weaker but is all there is before a token exists.
    """
    if user is not None:
        return f"user:{user.username}"
    client = request.client.host if request.client else "unknown"
    # X-Forwarded-For is only meaningful behind a proxy that sets it, and is
    # trivially spoofed otherwise, so the direct peer is preferred unless the
    # deployment is known to be proxied.
    return f"ip:{client}"


def rate_limit(bucket: str = "default"):
    """Dependency factory enforcing a request budget."""
    allowance, window = RATE_LIMITS.get(bucket, RATE_LIMITS["default"])

    async def _check(
        request: Request, user: User | None = Depends(optional_user)
    ) -> None:
        key = f"{bucket}:{_identity(request, user)}"
        try:
            retry_after = state.store().hit(key, allowance, window)
        except state.StateUnavailable as exc:
            # Fails OPEN, unlike authorisation. Refusing every request because
            # the counter store is down would turn a Redis outage into a full
            # outage, and the one limit that guards credentials -- login -- is
            # backed by account lockout in the user store regardless.
            logger.warning("rate limit for %r not enforced: %s", bucket, exc)
            return

        if retry_after:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {allowance} requests per "
                    f"{window} seconds for '{bucket}'. Retry in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )

    return _check


def reset_rate_limits() -> None:
    """Clear all shared state -- limits, revocations, liveness. For tests."""
    state.store().reset()
