"""
Security tests.

These lock down the decisions that are expensive to get wrong and easy to
regress: that auth fails closed, that there is no default signing key, that
roles actually gate something, and that error messages do not leak.
"""

from __future__ import annotations

import os

import jwt
import pytest

from app.core.security import (
    ACCESS_TOKEN_MINUTES,
    MIN_SECRET_LENGTH,
    ROLE_PERMISSIONS,
    Role,
    SecretNotConfigured,
    constant_time_equals,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_secret,
    hash_password,
    has_permission,
    password_problems,
    verify_password,
)


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setenv("VERISHIELD_JWT_SECRET", generate_secret())


class TestNoDefaultSecret:
    """
    A signing key with a working development default is how a system ends up
    deployed that anyone can mint tokens for: it works on a laptop, nobody
    revisits it, and the deployment is silently open.
    """

    @pytest.fixture
    def no_key(self, monkeypatch):
        """
        Remove every source of a signing key.

        Deleting the environment variable alone stopped being enough once
        `_secret()` gained a fallback to `settings.jwt_secret`, which
        pydantic-settings fills from backend/.env. On a machine that has been
        bootstrapped -- which is every machine that can actually run the
        service -- these tests then read the real key and passed for the wrong
        reason. The property under test is that no key means no tokens, so
        both sources have to go.
        """
        from app.core.config import settings

        monkeypatch.delenv("VERISHIELD_JWT_SECRET", raising=False)
        monkeypatch.setattr(settings, "jwt_secret", "")

    def test_token_creation_refuses_without_a_key(self, no_key):
        with pytest.raises(SecretNotConfigured):
            create_access_token("someone", Role.OPERATOR)

    def test_token_decoding_refuses_without_a_key(self, no_key):
        with pytest.raises(SecretNotConfigured):
            decode_token("anything")

    def test_a_short_key_is_refused(self, no_key, monkeypatch):
        monkeypatch.setenv("VERISHIELD_JWT_SECRET", "short")
        with pytest.raises(SecretNotConfigured):
            create_access_token("someone", Role.OPERATOR)

    def test_a_short_key_in_the_env_file_is_refused_too(self, no_key, monkeypatch):
        """The .env fallback must not be a way around the length floor."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "jwt_secret", "short")
        with pytest.raises(SecretNotConfigured):
            create_access_token("someone", Role.OPERATOR)

    def test_the_error_says_how_to_fix_it(self, no_key):
        with pytest.raises(SecretNotConfigured) as exc:
            create_access_token("someone", Role.OPERATOR)
        assert "token_urlsafe" in str(exc.value)

    def test_generated_keys_are_long_enough_and_unique(self):
        keys = {generate_secret() for _ in range(20)}
        assert len(keys) == 20
        assert all(len(k) >= MIN_SECRET_LENGTH for k in keys)


class TestPasswordHashing:
    def test_hash_is_not_the_password(self):
        hashed = hash_password("correct horse battery staple")
        assert "correct horse" not in hashed

    def test_same_password_hashes_differently(self):
        """Per-hash salt: identical passwords must not produce identical hashes."""
        a = hash_password("correct horse battery staple")
        b = hash_password("correct horse battery staple")
        assert a != b

    def test_verification_round_trips(self):
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed)
        assert not verify_password("Correct horse battery staple", hashed)

    def test_a_corrupt_hash_fails_rather_than_raising(self):
        """A damaged record must fail authentication, not the request."""
        assert verify_password("anything", "not-a-real-hash") is False

    @pytest.mark.parametrize(
        "password", ["short", "password123", "aaaaaaaaaaaaaaa", "verishield"]
    )
    def test_weak_passwords_are_rejected(self, password):
        assert password_problems(password)

    def test_a_long_varied_password_is_accepted(self):
        assert password_problems("tram-basalt-quiver-9481") == []


class TestTokens:
    def test_access_token_round_trips(self, signing_key):
        token = create_access_token("rajdeep", Role.REVIEWER)
        payload = decode_token(token)
        assert payload["sub"] == "rajdeep"
        assert payload["role"] == "reviewer"

    def test_a_refresh_token_is_rejected_where_an_access_token_belongs(self, signing_key):
        """
        Without this, a refresh token -- which lives a week -- would be accepted
        anywhere an access token is, turning a 30-minute credential into a
        seven-day one.
        """
        refresh, _jti = create_refresh_token("rajdeep")
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(refresh, expected_type="access")

    def test_a_token_signed_with_another_key_is_rejected(self, signing_key):
        forged = jwt.encode(
            {"sub": "attacker", "role": "admin", "type": "access",
             "exp": 9999999999, "jti": "x"},
            "a-different-key-entirely-that-is-long-enough",
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidSignatureError):
            decode_token(forged)

    def test_an_unsigned_token_is_rejected(self, signing_key):
        """The 'alg: none' attack -- rejected because algorithms are pinned."""
        unsigned = jwt.encode(
            {"sub": "attacker", "role": "admin", "type": "access",
             "exp": 9999999999, "jti": "x"},
            key="",
            algorithm="none",
        )
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(unsigned)

    def test_an_expired_token_is_rejected(self, signing_key):
        token = create_access_token("rajdeep", Role.OPERATOR, expires_minutes=-1)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)

    def test_every_token_carries_a_unique_id(self, signing_key):
        """A jti lets one token be revoked without invalidating all of them."""
        ids = {decode_token(create_access_token("a", Role.OPERATOR))["jti"] for _ in range(10)}
        assert len(ids) == 10

    def test_access_tokens_are_short_lived(self):
        assert ACCESS_TOKEN_MINUTES <= 60


class TestRoles:
    def test_an_operator_cannot_unmask_identity_numbers(self):
        """
        The boundary that makes these roles real rather than decorative.
        Responses mask Aadhaar and PAN numbers by default, and only someone
        justifying a decision has cause to see one in full.
        """
        assert not has_permission(Role.OPERATOR, "verify:reveal_identifiers")
        assert has_permission(Role.REVIEWER, "verify:reveal_identifiers")

    def test_an_operator_cannot_read_the_audit_trail(self):
        assert not has_permission(Role.OPERATOR, "audit:read")
        assert has_permission(Role.REVIEWER, "audit:read")

    def test_only_an_admin_manages_users(self):
        assert has_permission(Role.ADMIN, "users:manage")
        assert not has_permission(Role.REVIEWER, "users:manage")

    def test_every_role_can_submit_a_document(self):
        for role in Role:
            assert has_permission(role, "verify:submit")

    def test_permissions_grow_monotonically_with_role(self):
        """Each role holds everything the one below it does."""
        assert ROLE_PERMISSIONS[Role.OPERATOR] <= ROLE_PERMISSIONS[Role.REVIEWER]
        assert ROLE_PERMISSIONS[Role.REVIEWER] <= ROLE_PERMISSIONS[Role.ADMIN]

    def test_an_unknown_permission_is_never_granted(self):
        for role in Role:
            assert not has_permission(role, "system:shutdown")


class TestConstantTimeComparison:
    def test_equal_and_unequal_secrets(self):
        assert constant_time_equals("abc123", "abc123")
        assert not constant_time_equals("abc123", "abc124")

    def test_different_lengths_do_not_raise(self):
        assert not constant_time_equals("short", "considerably longer")


# --- endpoint-level authorisation ------------------------------------------


@pytest.fixture
def api(monkeypatch, signing_key):
    """
    A client with a stubbed user store.

    Stubbing the store rather than running MongoDB keeps these fast and
    hermetic. What is under test is the authorisation boundary, not the
    database behind it.
    """
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from app.core import auth as auth_module
    from app.core import users as users_module
    from app.core.users import User
    from app.main import app

    accounts = {
        name: User(
            username=name,
            password_hash=hash_password("tram-basalt-quiver-9481"),
            role=role,
            created_at=datetime.now(timezone.utc),
        )
        for name, role in (
            ("op", Role.OPERATOR),
            ("rev", Role.REVIEWER),
            ("boss", Role.ADMIN),
        )
    }

    class StubStore:
        available = True
        error = ""

        def get(self, username):
            return accounts.get(username.lower())

        def list_users(self):
            return list(accounts.values())

        def authenticate(self, username, password):
            user = accounts.get(username.lower())
            if user and verify_password(password, user.password_hash):
                return user, "ok"
            return None, "wrong password"

    stub = StubStore()
    # Every module that imported the store by value needs its own reference
    # replaced -- `from app.core.users import user_store` binds the object, not
    # the name, so patching one module does not reach the others.
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(users_module, "user_store", stub)
    monkeypatch.setattr(auth_module, "user_store", stub)
    monkeypatch.setattr(auth_routes, "user_store", stub)
    auth_module._REVOKED_JTI.clear()
    auth_module.reset_rate_limits()

    client = TestClient(app, raise_server_exceptions=False)

    def token_for(name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(name, accounts[name].role)}"}

    client.token_for = token_for  # type: ignore[attr-defined]
    return client


class TestEndpointAuthorisation:
    def test_unauthenticated_requests_are_refused(self, api):
        assert api.get("/api/v1/auth/me").status_code == 401

    def test_an_operator_is_refused_the_audit_trail(self, api):
        response = api.get("/api/v1/cases/recent", headers=api.token_for("op"))
        assert response.status_code == 403
        assert "audit:read" in response.json()["detail"]

    def test_a_reviewer_may_read_the_audit_trail(self, api):
        assert api.get(
            "/api/v1/cases/recent", headers=api.token_for("rev")
        ).status_code == 200

    def test_an_operator_is_refused_user_management(self, api):
        assert api.get("/api/v1/auth/users", headers=api.token_for("op")).status_code == 403

    def test_an_admin_may_manage_users(self, api):
        assert api.get("/api/v1/auth/users", headers=api.token_for("boss")).status_code == 200

    def test_me_reports_the_caller_permissions(self, api):
        body = api.get("/api/v1/auth/me", headers=api.token_for("rev")).json()
        assert body["role"] == "reviewer"
        assert "verify:reveal_identifiers" in body["permissions"]

    def test_a_disabled_account_is_refused(self, api, monkeypatch):
        from app.core import auth as auth_module

        auth_module.user_store.get("op").disabled = True
        assert api.get("/api/v1/auth/me", headers=api.token_for("op")).status_code == 401


class TestLoginDoesNotLeak:
    def test_unknown_user_and_wrong_password_are_indistinguishable(self, api):
        unknown = api.post(
            "/api/v1/auth/login", data={"username": "nobody", "password": "whatever12345"}
        )
        wrong = api.post(
            "/api/v1/auth/login", data={"username": "op", "password": "wrong-password-1"}
        )
        assert unknown.status_code == wrong.status_code == 401
        # Identical text, so error messages cannot be used to enumerate accounts.
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_a_correct_login_returns_tokens(self, api):
        response = api.post(
            "/api/v1/auth/login",
            data={"username": "rev", "password": "tram-basalt-quiver-9481"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["role"] == "reviewer"


class TestFailClosed:
    """
    Everywhere else in this project a missing component degrades gracefully.
    Authorisation must not: an unreachable user store means the boundary cannot
    be checked, and an unchecked boundary is not a boundary.
    """

    def test_an_unreachable_user_store_refuses_requests(self, api, monkeypatch):
        from app.core import auth as auth_module

        monkeypatch.setattr(type(auth_module.user_store), "available", False)
        auth_module.reset_rate_limits()
        response = api.get("/api/v1/auth/me", headers=api.token_for("rev"))
        assert response.status_code == 503
        assert "refused rather than admitted" in response.json()["detail"]


class TestSecurityHeaders:
    def test_headers_are_present_on_every_response(self, api):
        headers = api.get("/health").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]

    def test_responses_are_not_cached(self, api):
        """These responses describe people's identity documents."""
        assert api.get("/health").headers["cache-control"] == "no-store"
