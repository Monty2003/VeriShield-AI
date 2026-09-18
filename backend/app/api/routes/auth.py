"""
Authentication endpoints.

    POST /auth/login     -> access + refresh tokens
    POST /auth/refresh   -> a new access token
    POST /auth/logout    -> end the sign-in: access and refresh tokens alike
    GET  /auth/me        -> who am I, and what may I do
    POST /auth/users     -> create an account (admin only)

One thing to notice in `login`: every failure returns the same message. The
store distinguishes "no such user" from "wrong password" from "account locked"
for the log, and the client is told none of it. Anything more specific lets an
attacker enumerate valid usernames by reading error text.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from app.core import state
from app.core.auth import (
    current_user,
    is_revoked,
    oauth2_scheme,
    rate_limit,
    requires,
    revoke_session,
)
from app.core.security import (
    ROLE_PERMISSIONS,
    Role,
    SecretNotConfigured,
    create_access_token,
    create_refresh_token,
    decode_token,
    new_session_id,
    password_problems,
)
from app.core.users import User, user_store

logger = logging.getLogger(__name__)
router = APIRouter()

# Deliberately identical for every failure mode.
_LOGIN_FAILED = "Incorrect username or password."


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    role: str


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    role: Role = Role.OPERATOR
    full_name: str = ""


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("login"))],
)
def login(form: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    """Exchange a username and password for tokens."""
    try:
        user, reason = user_store.authenticate(form.username, form.password)
    except SecretNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if user is None:
        if reason == "user store unavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                # The old text here said "docker compose up -d". There is no
                # compose file any more -- MongoDB Atlas is the data layer for
                # local development as well as production -- so that advice
                # sent anyone who hit this looking for a file that does not
                # exist, instead of at the setting that was actually wrong.
                detail=(
                    "The user store is unreachable, so logins cannot be "
                    "processed. Check VERISHIELD_MONGO_URL in backend/.env, "
                    "and that this machine's IP is allowed in the Atlas "
                    "cluster's Network Access list."
                ),
            )
        # Logged with the real reason; returned without it.
        logger.info("login failed for %r: %s", form.username, reason)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_FAILED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # One sid for the whole sign-in, so signing out can end all of it.
        sid = new_session_id()
        access = create_access_token(user.username, user.role, session_id=sid)
        refresh, _jti = create_refresh_token(user.username, session_id=sid)
    except SecretNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    from app.core.security import ACCESS_TOKEN_MINUTES

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=ACCESS_TOKEN_MINUTES * 60,
        role=user.role.value,
    )


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_token(refresh_token: str) -> TokenResponse:
    """
    Exchange a refresh token for a new access token.

    The refresh token is decoded with `expected_type="refresh"`, so an access
    token cannot be presented here and a refresh token cannot be presented to a
    protected endpoint. Without that check the two become interchangeable and
    the short access lifetime stops meaning anything.
    """
    import jwt

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except SecretNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from exc

    # Before this check existed, signing out ended nothing that mattered: the
    # refresh token went on minting access tokens for the rest of its week.
    try:
        if is_revoked(payload):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This session has been signed out.",
            )
    except state.StateUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="The session store is unreachable, so the refresh cannot be checked.",
        ) from exc

    if not user_store.available:
        raise HTTPException(
            status_code=503, detail="The user store is unreachable."
        )

    user = user_store.get(payload["sub"])
    if user is None or user.disabled or user.locked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is no longer active.",
        )

    from app.core.security import ACCESS_TOKEN_MINUTES

    return TokenResponse(
        # Carries the sid forward, so a later sign-out still reaches it.
        access_token=create_access_token(
            user.username, user.role, session_id=payload.get("sid")
        ),
        expires_in=ACCESS_TOKEN_MINUTES * 60,
        role=user.role.value,
    )


@router.post("/auth/logout")
def logout(
    token: str | None = Depends(oauth2_scheme),
    _user: User = Depends(current_user),
) -> dict[str, object]:
    """
    End this sign-in on the server.

    This used to answer {"revoked": true} without revoking anything -- the
    function never saw the token, so it had nothing to revoke. Now the session
    id carried by the token is revoked, which ends the access token presented,
    every other access token refreshed from the same sign-in, and the refresh
    token itself.
    """
    # current_user has already verified this token; decoding again only reads
    # its ids.
    payload = decode_token(token or "", expected_type="access")
    try:
        scope = revoke_session(payload)
    except state.StateUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The session store is unreachable, so the sign-out could not be "
                "recorded. The session is still valid -- try again."
            ),
        ) from exc

    return {
        "revoked": True,
        "scope": scope,
        "note": (
            "The whole sign-in has ended: its access and refresh tokens are "
            "rejected from now on."
            if scope == "session"
            else "This token predates session ids, so only it could be revoked; "
            "discard its refresh token on the client."
        ),
    }


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, object]:
    """The caller's account and exactly what it may do."""
    return {
        **user.public(),
        "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set())),
    }


@router.post("/auth/users", status_code=status.HTTP_201_CREATED)
def create_user(
    request: CreateUserRequest,
    _admin: User = Depends(requires("users:manage")),
) -> dict[str, object]:
    """Create an account. Admin only."""
    problems = password_problems(request.password)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password {', and '.join(problems)}.",
        )

    if not user_store.available:
        raise HTTPException(status_code=503, detail="The user store is unreachable.")

    if user_store.get(request.username) is not None:
        raise HTTPException(status_code=409, detail="That username is taken.")

    user = user_store.create(
        request.username, request.password, request.role, request.full_name
    )
    if user is None:
        raise HTTPException(status_code=503, detail="Could not create the account.")
    return user.public()


@router.get("/auth/users")
def list_users(_admin: User = Depends(requires("users:manage"))) -> dict[str, object]:
    """All accounts. Admin only. Never includes password hashes."""
    return {"users": [u.public() for u in user_store.list_users()]}


@router.post("/auth/password")
def change_password(
    request: ChangePasswordRequest, user: User = Depends(current_user)
) -> dict[str, object]:
    """
    Change your own password.

    The current password is required even though the caller is already
    authenticated: a stolen access token should not be enough to take over the
    account permanently.
    """
    verified, _reason = user_store.authenticate(user.username, request.current_password)
    if verified is None:
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    problems = password_problems(request.new_password)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password {', and '.join(problems)}.",
        )

    if not user_store.set_password(user.username, request.new_password):
        raise HTTPException(status_code=503, detail="Could not update the password.")
    return {"changed": True}
