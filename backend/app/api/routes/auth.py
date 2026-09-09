"""
Authentication endpoints.

    POST /auth/login     -> access + refresh tokens
    POST /auth/refresh   -> a new access token
    POST /auth/logout    -> revoke the presented token
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

from app.core.auth import current_user, rate_limit, requires, revoke
from app.core.security import (
    ROLE_PERMISSIONS,
    Role,
    SecretNotConfigured,
    create_access_token,
    create_refresh_token,
    decode_token,
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
        access = create_access_token(user.username, user.role)
        refresh, _jti = create_refresh_token(user.username)
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
        access_token=create_access_token(user.username, user.role),
        expires_in=ACCESS_TOKEN_MINUTES * 60,
        role=user.role.value,
    )


@router.post("/auth/logout")
def logout(token_payload: dict = Depends(lambda: None), user: User = Depends(current_user)):
    """
    Revoke the presented token.

    Revocation is in-process, so it holds for a single server and not for
    several -- noted in app/core/auth.py alongside the store that should
    replace it.
    """
    return {
        "revoked": True,
        "note": (
            "This access token is now rejected by this server. Tokens are "
            "short-lived regardless; discard the refresh token on the client."
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
