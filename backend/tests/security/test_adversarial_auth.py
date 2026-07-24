"""Adversarial tests for auth, tokens, and RBAC boundary conditions.

Every test here probes a concrete, code-verified edge case — not a generic
happy path. The behaviors asserted are grounded in the actual implementation:

* ``decode_token`` uses PyJWT defaults (exp verified; aud/iss NOT; no leeway).
* ``require_roles`` lets ``super_admin`` bypass every check.
* Password-change staleness compares ``token_iat <= pwd_change_ts`` (boundary-inclusive).
* Token revocation is MySQL-backed; blacklist + password-change staleness checks are
  performed against the database.
* ``get_current_user`` accepts tokens WITHOUT a ``jti`` (warns, cannot revoke).
* Refresh endpoint does NOT rotate the refresh token — a stolen cookie replays forever.
* ``authenticate_user`` burns a dummy argon2id hash to close timing enumeration.
* ``require_roles`` matches role CODES (strings), and ``roles_api`` passes the literal
  ``"admin"`` rather than the constant — both paths must resolve to the same role.
"""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.identity.authentication import (
    is_token_blacklisted,
    record_password_change,
)
from app.platform.config.settings import settings
from app.platform.security.tokens import decode_token


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _admin(client: TestClient) -> dict[str, str]:
    return _login(client, "admin", "SuperAdminPass1")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _create_user(
    client: TestClient,
    admin_h: dict[str, str],
    username: str,
    password: str,
    real_name: str,
    role_codes: list[str] | None = None,
) -> int:
    resp = client.post(
        "/api/v1/users",
        headers=admin_h,
        json={"username": username, "password": password, "real_name": real_name},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["data"]["id"]
    for code in role_codes or []:
        r = client.post(
            f"/api/v1/users/{uid}/roles", headers=admin_h, json={"role_code": code}
        )
        assert r.status_code == 201, r.text
    return uid


# ---------------------------------------------------------------------------
# JWT decode semantics — what PyJWT defaults actually enforce
# ---------------------------------------------------------------------------


class TestJwtDecodeSemantics:
    """The decoder relies on PyJWT defaults. Verify exactly what is and isn't checked."""

    def test_expired_token_rejected(self):
        """exp IS verified by default — an expired token must raise."""
        # Build a token already expired by 1 second.
        payload = {
            "sub": "1",
            "jti": uuid4().hex,
            "iat": int(time.time()) - 60,
            "exp": int(time.time()) - 1,
            "type": "access",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(jwt.PyJWTError):
            decode_token(token)

    def test_aud_claim_rejected_when_decoder_has_no_audience(self):
        """PyJWT's default: a token carrying an ``aud`` claim is REJECTED when the decoder
        does not pass ``audience=``. ``decode_token`` passes no audience, so any token
        with an aud claim is hard-rejected. This is a sharp edge: a third-party IdP
        that includes aud cannot be consumed by this decoder at all."""
        payload = {
            "sub": "1",
            "jti": uuid4().hex,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
            "aud": "some-audience",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(jwt.PyJWTError):
            decode_token(token)

    def test_iss_claim_not_verified(self):
        """iss is NOT verified — a token claiming a foreign issuer is accepted."""
        payload = {
            "sub": "1",
            "jti": uuid4().hex,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
            "iss": "https://attacker.example.com",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        decoded = decode_token(token)
        assert decoded["iss"] == "https://attacker.example.com"

    def test_wrong_secret_rejected(self):
        """Signature verification IS enforced — a token signed with the wrong key is rejected."""
        payload = {
            "sub": "1",
            "jti": uuid4().hex,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
        }
        token = jwt.encode(payload, "totally-wrong-secret", algorithm=settings.jwt_algorithm)
        with pytest.raises(jwt.PyJWTError):
            decode_token(token)

    def test_alg_none_rejected(self):
        """The decoder pins algorithms=[HS256]; an alg=none forgery must not be accepted."""
        # PyJWT rejects alg=none when algorithms is explicitly set and "none" is not in it.
        payload = {
            "sub": "1",
            "jti": uuid4().hex,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
        }
        # Manually craft an alg=none token (no signature segment).
        import base64
        import json

        def b64(obj):
            return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()

        token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}."
        with pytest.raises(jwt.PyJWTError):
            decode_token(token)


# ---------------------------------------------------------------------------
# Token type confusion — refresh vs access must not cross-use
# ---------------------------------------------------------------------------


class TestTokenTypeConfusion:
    def test_refresh_token_as_bearer_rejected(self):
        """A refresh token presented as a Bearer access token must be 401 INVALID_TOKEN."""
        client = _client()
        # Login sets the refresh cookie; capture it.
        resp = client.post(
            "/api/v1/auth/sessions",
            json={"username": "admin", "password": "SuperAdminPass1"},
        )
        refresh_cookie = resp.cookies.get("dwg_refresh_token")
        assert refresh_cookie, "login must set a refresh cookie"
        # Use the refresh token as a Bearer header on a protected endpoint.
        r = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_cookie}"}
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_TOKEN"

    def test_access_token_as_refresh_cookie_rejected(self):
        """An access token presented as a refresh cookie must be 401 INVALID_TOKEN."""
        client = _client()
        h = _admin(client)
        access_token = h["Authorization"].removeprefix("Bearer ")
        r = client.post(
            "/api/v1/auth/tokens/refresh",
            cookies={"dwg_refresh_token": access_token},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_TOKEN"

    def test_garbage_token_rejected_not_500(self):
        """A non-JWT string must yield 401, never a 500."""
        client = _client()
        r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# jti handling — tokens without jti are accepted but cannot be revoked
# ---------------------------------------------------------------------------


class TestJtiOptionality:
    def test_token_without_jti_accepted_with_warning(self):
        """A hand-crafted access token with no jti is accepted (pre-rollout compat).

        get_current_user logs a warning but does NOT reject. This means logout
        is a no-op for such tokens — they remain valid until expiry.
        """
        client = _client()
        payload = {
            "sub": "1",  # the seeded super_admin
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
            # deliberately no jti
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["data"]["username"] == "admin"

    def test_logout_noop_for_jtiless_token(self):
        """Logout cannot blacklist a token without jti; the token stays usable."""
        client = _client()
        payload = {
            "sub": "1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        h = {"Authorization": f"Bearer {token}"}
        # Logout returns 204 but blacklisting is silently skipped (no jti).
        r = client.delete("/api/v1/auth/sessions/current", headers=h)
        assert r.status_code == 204
        # The same token still works afterwards — revocation was a no-op.
        r2 = client.get("/api/v1/auth/me", headers=h)
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Password-change staleness — the boundary is INCLUSIVE (iat <= pwd_change_ts)
# ---------------------------------------------------------------------------


class TestPasswordChangeStaleness:
    def test_token_issued_after_password_change_still_valid(self, db):
        """A token whose iat is strictly AFTER pwd_change_ts must remain valid."""
        client = _client()
        _admin(client)
        # Record a password change "in the past" relative to a fresh token.
        record_password_change(db, 1)
        db.commit()
        # Slight delay so the new token's iat is strictly greater.
        time.sleep(1.1)
        new_h = _admin(client)  # fresh login -> fresh token iat > pwd_change_ts
        r = client.get("/api/v1/auth/me", headers=new_h)
        assert r.status_code == 200, "token issued after pwd change must stay valid"

    def test_token_issued_at_exact_boundary_rejected(self, db):
        """iat == pwd_change_ts is rejected because the check is ``iat <= pwd_change_ts``."""
        client = _client()
        h = _admin(client)
        # Capture the iat of the just-issued token, then set password_changed_at
        # to that exact iat timestamp on the user.
        token = h["Authorization"].removeprefix("Bearer ")
        decoded = decode_token(token)
        iat = float(decoded["iat"])
        # Set password_changed_at using the db fixture (verified to work in
        # test_token_issued_after_password_change_still_valid).
        from datetime import UTC, datetime

        from app.modules.identity.interface import User

        user = db.get(User, 1)
        assert user is not None, "Admin user (id=1) not found in test DB"
        user.password_changed_at = datetime.fromtimestamp(iat, tz=UTC)
        db.commit()
        # Now the token must be rejected: iat <= pwd_change_ts (equal).
        resp = client.get("/api/v1/auth/me", headers=h)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json()["error"]["code"] == "TOKEN_REVOKED"


# ---------------------------------------------------------------------------
# Token revocation — MySQL-backed (no fail-open; DB is source of truth)
# ---------------------------------------------------------------------------


class TestTokenRevocation:
    def test_blacklist_database_error_does_not_fail_open(self, db, monkeypatch):
        def fail_get(*_args, **_kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "get", fail_get)

        with pytest.raises(RuntimeError, match="database unavailable"):
            is_token_blacklisted(db, "untrusted-jti")

    def test_blacklisted_token_is_revoked(self):
        """Logout blacklists the token; subsequent requests are rejected."""
        client = _client()
        h = _admin(client)
        # Logout normally blacklists the token.
        client.delete("/api/v1/auth/sessions/current", headers=h)
        # The token is now revoked.
        assert client.get("/api/v1/auth/me", headers=h).status_code == 401

    def test_password_change_revokes_tokens(self):
        """After a password change, existing tokens are rejected."""
        client = _client()
        h = _admin(client)
        new_password = "NewPassphrase123!"
        # Change password (min 12 chars with upper+lower+digit)
        resp_patch = client.patch(
            "/api/v1/auth/password",
            headers=h,
            json={"current_password": "SuperAdminPass1", "new_password": new_password},
        )
        assert resp_patch.status_code == 200, f"Password change failed: {resp_patch.text}"
        # The pre-change token must be rejected.
        resp = client.get("/api/v1/auth/me", headers=h)
        assert resp.status_code == 401
        # Login with new password to restore original
        login_resp = client.post(
            "/api/v1/auth/sessions",
            json={"username": "admin", "password": new_password},
        )
        assert login_resp.status_code == 201
        new_h = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}
        client.patch(
            "/api/v1/auth/password",
            headers=new_h,
            json={"current_password": new_password, "new_password": "SuperAdminPass1"},
        )


# ---------------------------------------------------------------------------
# Refresh-token replay — refresh is NOT rotated
# ---------------------------------------------------------------------------


class TestRefreshTokenReplay:
    def test_refresh_token_not_rotated_and_replayable(self):
        """The refresh endpoint issues a new access token but reuses the SAME refresh
        token. A stolen refresh cookie can be replayed indefinitely until expiry
        or password change."""
        client = _client()
        login_resp = client.post(
            "/api/v1/auth/sessions",
            json={"username": "admin", "password": "SuperAdminPass1"},
        )
        assert login_resp.status_code == 201
        original_refresh = login_resp.cookies.get("dwg_refresh_token")
        assert original_refresh

        # First refresh — succeeds, returns a new access token.
        r1 = client.post(
            "/api/v1/auth/tokens/refresh",
            cookies={"dwg_refresh_token": original_refresh},
        )
        assert r1.status_code == 200
        access1 = r1.json()["data"]["access_token"]
        # The Set-Cookie may or may not overwrite; the original cookie must STILL work
        # because refresh is not rotated. Replay the original cookie.
        r2 = client.post(
            "/api/v1/auth/tokens/refresh",
            cookies={"dwg_refresh_token": original_refresh},
        )
        assert r2.status_code == 200, "refresh token not rotated — original must remain usable"
        access2 = r2.json()["data"]["access_token"]
        # New access tokens are distinct (different jti), but the refresh token was reused.
        assert access1 != access2

    def test_refresh_after_password_change_rejected(self):
        """A password change must invalidate the refresh cookie too (staleness check)."""
        client = _client()
        login_resp = client.post(
            "/api/v1/auth/sessions",
            json={"username": "admin", "password": "SuperAdminPass1"},
        )
        refresh = login_resp.cookies.get("dwg_refresh_token")
        # Change the admin password.
        new_pass = "NewSuperAdminPass1"
        h = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}
        cp = client.patch(
            "/api/v1/auth/password",
            headers=h,
            json={"current_password": "SuperAdminPass1", "new_password": new_pass},
        )
        assert cp.status_code == 200, cp.text
        # The old refresh cookie must now be stale -> rejected.
        r = client.post(
            "/api/v1/auth/tokens/refresh", cookies={"dwg_refresh_token": refresh}
        )
        assert r.status_code == 401
        # Restore the seed password so other tests' login still works.
        restore_h = _login(client, "admin", new_pass)
        client.patch(
            "/api/v1/auth/password",
            headers=restore_h,
            json={"current_password": new_pass, "new_password": "SuperAdminPass1"},
        )

    def test_refresh_missing_cookie_rejected(self):
        """No refresh cookie -> 401 INVALID_TOKEN, not 422."""
        client = _client()
        r = client.post("/api/v1/auth/tokens/refresh")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# Timing-side-channel resistance on login
# ---------------------------------------------------------------------------


class TestLoginTimingResistance:
    def test_nonexistent_user_returns_401_not_404(self):
        """Login must return 401 INVALID_CREDENTIALS (never 404) for an unknown user,
        to avoid user enumeration via status code."""
        client = _client()
        r = client.post(
            "/api/v1/auth/sessions",
            json={"username": _unique("ghost"), "password": "WhateverPass1"},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_nonexistent_user_burns_dummy_hash(self):
        """authenticate_user must call verify_password against the dummy hash for a
        non-existent user. We assert this indirectly: the login call for a
        non-existent user takes roughly the same time as a wrong-password login
        for a real user (both run one argon2id verify)."""
        client = _client()
        # Warm up the argon2id params first so parameter negotiation cost is excluded.
        client.post(
            "/api/v1/auth/sessions",
            json={"username": "admin", "password": "WrongPassword1234"},
        )
        import time as _time

        t0 = _time.perf_counter()
        for _ in range(3):
            client.post(
                "/api/v1/auth/sessions",
                json={"username": "admin", "password": "WrongPassword1234"},
            )
        real_wrong = (_time.perf_counter() - t0) / 3

        t0 = _time.perf_counter()
        for _ in range(3):
            client.post(
                "/api/v1/auth/sessions",
                json={"username": _unique("ghost"), "password": "WrongPassword1234"},
            )
        ghost = (_time.perf_counter() - t0) / 3

        # Both paths run exactly one argon2id verify (m=65536,t=3,p=4). Allow generous
        # tolerance — we only assert they're the same ORDER of magnitude, not that
        # login is fast. A missing dummy hash would make ghost ~100x faster.
        ratio = max(real_wrong, ghost) / max(min(real_wrong, ghost), 1e-6)
        assert ratio < 5.0, (
            f"timing discrepancy too large: real_wrong={real_wrong:.3f}s ghost={ghost:.3f}s "
            f"ratio={ratio:.2f} — dummy-hash burn likely missing"
        )


# ---------------------------------------------------------------------------
# require_roles semantics — super_admin bypass + literal "admin" string
# ---------------------------------------------------------------------------


class TestRequireRolesSemantics:
    def test_super_admin_bypasses_any_require_roles(self):
        """super_admin must pass require_roles(...) regardless of allowed_roles."""
        client = _client()
        # roles_api GET /roles requires (ROLE_SUPER_ADMIN, "admin").
        h = _admin(client)  # admin is super_admin (the seed).
        r = client.get("/api/v1/roles", headers=h)
        assert r.status_code == 200

    def test_viewer_cannot_list_users(self):
        """A viewer must not satisfy the admin requirement for GET /users."""
        client = _client()
        admin_h = _admin(client)
        uname = _unique("aud")
        _create_user(client, admin_h, uname, "AuditorPass1234", "Aud", ["viewer"])
        # Login as the viewer.
        h = _login(client, uname, "AuditorPass1234")
        r = client.get("/api/v1/users", headers=h)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FORBIDDEN"

    def test_viewer_cannot_list_admin_audit_logs(self):
        """Audit logs remain inside the admin-only data and storage boundary."""
        client = _client()
        admin_h = _admin(client)
        uname = _unique("aud2")
        _create_user(client, admin_h, uname, "AuditorPass1234", "Aud2", ["viewer"])
        h = _login(client, uname, "AuditorPass1234")
        r = client.get("/api/v1/audit-logs", headers=h)
        assert r.status_code == 403

    def test_roles_api_literal_admin_string_works(self):
        """roles_api passes the literal string "admin" (not the constant) to require_roles.
        A user with the "admin" role code must satisfy it — verify the string path resolves."""
        client = _client()
        admin_h = _admin(client)
        uname = _unique("adm")
        _create_user(client, admin_h, uname, "AdminPass1234", "Adm", ["admin"])
        h = _login(client, uname, "AdminPass1234")
        # GET /roles is gated by require_roles(ROLE_SUPER_ADMIN, "admin").
        r = client.get("/api/v1/roles", headers=h)
        assert r.status_code == 200, "literal 'admin' string must match the role code"

    def test_admin_can_create_roles(self):
        """The simplified model gives admin and super_admin equal control."""
        client = _client()
        admin_h = _admin(client)
        uname = _unique("adm3")
        _create_user(client, admin_h, uname, "AdminPass1234", "Adm3", ["admin"])
        h = _login(client, uname, "AdminPass1234")
        r = client.post(
            "/api/v1/roles",
            headers=h,
            json={"code": _unique("r"), "name": "R"},
        )
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# Self-protection guards
# ---------------------------------------------------------------------------


class TestSelfProtectionGuards:
    def test_admin_cannot_disable_self_via_disable_request(self):
        client = _client()
        admin_h = _admin(client)
        me = client.get("/api/v1/auth/me", headers=admin_h).json()["data"]["id"]
        r = client.post(f"/api/v1/users/{me}/disable-requests", headers=admin_h)
        assert r.status_code == 400

    def test_admin_cannot_disable_self_via_patch_status(self):
        client = _client()
        admin_h = _admin(client)
        me = client.get("/api/v1/auth/me", headers=admin_h).json()["data"]["id"]
        r = client.patch(f"/api/v1/users/{me}", headers=admin_h, json={"status": "disabled"})
        assert r.status_code == 400

    def test_admin_cannot_delete_self(self):
        client = _client()
        admin_h = _admin(client)
        me = client.get("/api/v1/auth/me", headers=admin_h).json()["data"]["id"]
        r = client.delete(f"/api/v1/users/{me}", headers=admin_h)
        assert r.status_code == 400

    def test_admin_cannot_remove_own_role(self):
        client = _client()
        admin_h = _admin(client)
        me = client.get("/api/v1/auth/me", headers=admin_h).json()["data"]
        # Find the super_admin role id on the user.
        super_role_id = next(r["id"] for r in me["roles"] if r["code"] == "super_admin")
        r = client.delete(
            f"/api/v1/users/{me['id']}/roles/{super_role_id}", headers=admin_h
        )
        assert r.status_code == 400

    def test_user_cannot_set_own_status_to_deleted_via_patch(self):
        """PATCH /users/{id} only accepts status in {active, disabled} — deleted is not settable."""
        client = _client()
        admin_h = _admin(client)
        me = client.get("/api/v1/auth/me", headers=admin_h).json()["data"]["id"]
        r = client.patch(f"/api/v1/users/{me}", headers=admin_h, json={"status": "deleted"})
        assert r.status_code == 422  # Literal["active","disabled"] rejects "deleted"


# ---------------------------------------------------------------------------
# Disabled-user token rejection mid-session
# ---------------------------------------------------------------------------


class TestDisabledUserTokenRejection:
    def test_existing_token_rejected_after_user_disabled(self):
        """An access token issued before disablement must be rejected on next use."""
        client = _client()
        admin_h = _admin(client)
        uname = _unique("u")
        uid = _create_user(client, admin_h, uname, "UserPass1234A", "U", ["operator"])
        h = _login(client, uname, "UserPass1234A")
        # Confirm token works.
        assert client.get("/api/v1/auth/me", headers=h).status_code == 200
        # Admin disables the user.
        r = client.post(f"/api/v1/users/{uid}/disable-requests", headers=admin_h)
        assert r.status_code == 200
        # The previously-valid token must now be rejected.
        r2 = client.get("/api/v1/auth/me", headers=h)
        assert r2.status_code == 401
        assert r2.json()["error"]["code"] == "USER_NOT_ACTIVE"

    def test_disabled_user_cannot_login(self):
        client = _client()
        admin_h = _admin(client)
        uname = _unique("u2")
        uid = _create_user(client, admin_h, uname, "UserPass1234A", "U2", ["operator"])
        # Disable the user via the disable-request endpoint.
        client.post(f"/api/v1/users/{uid}/disable-requests", headers=admin_h)
        r = client.post(
            "/api/v1/auth/sessions", json={"username": uname, "password": "UserPass1234A"}
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
