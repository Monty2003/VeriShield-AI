"""
Request-level authentication and authorisation.

Every protected endpoint depends on `current_user`, and every endpoint that
does something privileged depends on `requires(...)` naming the permission it
needs. Naming the permission rather than the role is what keeps the two from
drifting: adding a role means editing one table, not auditing every route for
`if role == ...`.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import Role, SecretNotConfigured, decode_token, has_permission
from app.core.users import User, user_store

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# Revoked token ids. In-process, which is correct for one server and wrong for
# several -- a token revoked on one worker stays valid on the others. Redis is
# already a dependency of this project and is where this belongs before it runs
# behind more than one process.
_REVOKED_JTI: set[str] = set()


def revoke(jti: str) -> None:
    _REVOKED_JTI.add(jti)


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

    if payload.get("jti") in _REVOKED_JTI:
        raise _unauthorised("Token has been revoked.")

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

_HITS: dict[str, deque[float]] = defaultdict(deque)


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
        now = time.monotonic()
        hits = _HITS[key]

        while hits and now - hits[0] > window:
            hits.popleft()

        if len(hits) >= allowance:
            retry_after = int(window - (now - hits[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {allowance} requests per "
                    f"{window} seconds for '{bucket}'. Retry in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)

    return _check


def reset_rate_limits() -> None:
    """Clear all buckets. For tests."""
    _HITS.clear()
