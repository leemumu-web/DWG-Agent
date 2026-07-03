"""Deep verification of known-limitation claims.

Each test rigorously proves or disproves a claimed limitation.
Claims that turn out to be FALSE (i.e., the feature actually works)
are reassigned as PASSING. Claims that are TRUE become confirmed bugs.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.main import app


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _login(client: TestClient, username: str = "admin", password: str = "SuperAdminPass1") -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# ===========================================================================
# CLAIM 1: "Password change does not revoke existing access tokens"
# ===========================================================================


def test_password_change_keeps_old_access_token_valid():
    """PROVE: after password change, old access token still works.

    This is the CURRENT behaviour. Whether it's a bug depends on your
    threat model.  For Stage 1 it's acceptable; for production you'd
    want per-user ``token_version`` invalidation.
    """
    client = _client()

    # Login and get a token
    login1 = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    old_token = login1.json()["data"]["access_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # Change password
    client.patch(
        "/api/v1/auth/password",
        headers=old_headers,
        json={"current_password": "SuperAdminPass1", "new_password": "NewPassphrase123"},
    )

    # Old token STILL works (CURRENT BEHAVIOUR)
    me = client.get("/api/v1/auth/me", headers=old_headers)
    assert me.status_code == 200, f"Old token rejected: {me.text}"

    # New password works for fresh login
    client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "NewPassphrase123"}
    )
    # Restore original password for other tests
    new_headers = _login(client, "admin", "NewPassphrase123")
    client.patch(
        "/api/v1/auth/password",
        headers=new_headers,
        json={"current_password": "NewPassphrase123", "new_password": "SuperAdminPass1"},
    )


def test_password_change_does_not_blacklist_old_tokens():
    """PROVE: old token jti is NOT in the blacklist after password change.

    The token remains valid because password change only updates the
    password_hash — it doesn't call blacklist_access_token().
    """
    client = _client()

    login1 = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    old_token = login1.json()["data"]["access_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # Change password
    client.patch(
        "/api/v1/auth/password",
        headers=old_headers,
        json={"current_password": "SuperAdminPass1", "new_password": "TempPass45678"},
    )

    # Old token still works on MULTIPLE endpoints
    for path in ("/api/v1/auth/me", "/api/v1/projects", "/api/v1/files"):
        resp = client.get(path, headers=old_headers)
        assert resp.status_code == 200, f"{path} rejected: {resp.text}"

    # Restore
    client.patch(
        "/api/v1/auth/password",
        headers=old_headers,
        json={"current_password": "TempPass45678", "new_password": "SuperAdminPass1"},
    )


# ===========================================================================
# CLAIM 2: "Refresh token is not rotated on refresh"
# ===========================================================================


def test_refresh_cookie_persists_but_not_rotated():
    """CONFIRMED: refresh issues new access token but does NOT rotate the cookie.

    The same refresh JWT stays in the cookie for its entire 14-day lifetime.
    A stolen refresh cookie can be used repeatedly until expiry or logout.
    Refresh-token rotation (issue new refresh + invalidate old) is Stage 2+.
    """
    client = _client()

    login = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    assert login.status_code == 201
    old_refresh = client.cookies.get("dwg_refresh_token")
    assert old_refresh, "Login must set refresh cookie"

    # Refresh — access token changes, refresh cookie unchanged
    refresh = client.post("/api/v1/auth/tokens/refresh")
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["data"]["access_token"] != login.json()["data"]["access_token"]

    # Cookie is the same (not rotated)
    new_refresh = client.cookies.get("dwg_refresh_token")
    assert new_refresh == old_refresh, (
        "Refresh cookie should persist unchanged (no rotation in Stage 1)"
    )

    # Multiple refreshes all succeed with same cookie
    for _ in range(2):
        r = client.post("/api/v1/auth/tokens/refresh")
        assert r.status_code == 200, r.text


# ===========================================================================
# CLAIM 3: "update_user_api has no super_admin target guard"
# ===========================================================================


def test_admin_can_modify_super_admin_real_name():
    """FIXED: admin can NO LONGER change super_admin's real_name via PATCH."""
    client = _client()
    admin_headers = _login(client)

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    # Create another admin
    admin2_user = _unique("mod-admin")
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": admin2_user,
            "password": "TestPass1234",
            "real_name": "Modify Admin",
        },
    )
    admin2_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{admin2_id}/roles",
        headers=admin_headers,
        json={"role_code": "admin"},
    )
    admin2_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": admin2_user, "password": "TestPass1234"},
    )
    admin2_headers = {"Authorization": f"Bearer {admin2_login.json()['data']['access_token']}"}

    # Admin2 tries to change super_admin's real_name — NOW BLOCKED
    resp = client.patch(
        f"/api/v1/users/{super_admin_id}",
        headers=admin2_headers,
        json={"real_name": "HACKED_NAME"},
    )
    assert resp.status_code == 400, f"Expected rejection: {resp.text}"
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


def test_admin_cannot_disable_super_admin_via_patch_status():
    """VERIFIED (now FIXED): admin can NO LONGER disable super_admin via PATCH.

    After adding ``_require_super_admin_target`` to ``update_user_api``,
    the PATCH endpoint correctly rejects non-super_admin users from modifying
    super_admin accounts.
    """
    client = _client()
    admin_headers = _login(client)

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("status-admin")
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": admin2_user,
            "password": "TestPass1234",
            "real_name": "Status Admin",
        },
    )
    admin2_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{admin2_id}/roles",
        headers=admin_headers,
        json={"role_code": "admin"},
    )
    admin2_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": admin2_user, "password": "TestPass1234"},
    )
    admin2_headers = {"Authorization": f"Bearer {admin2_login.json()['data']['access_token']}"}

    resp = client.patch(
        f"/api/v1/users/{super_admin_id}",
        headers=admin2_headers,
        json={"status": "disabled"},
    )
    # NOW correctly rejected
    assert resp.status_code == 400, (
        f"Expected PATCH status=disabled on super_admin to be rejected: {resp.text}"
    )
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


def test_admin_cannot_modify_super_admin_name_via_patch():
    """VERIFIED (now FIXED): admin cannot change super_admin's real_name via PATCH."""
    client = _client()
    admin_headers = _login(client)

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("name-admin")
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": admin2_user,
            "password": "TestPass1234",
            "real_name": "Name Admin",
        },
    )
    admin2_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{admin2_id}/roles",
        headers=admin_headers,
        json={"role_code": "admin"},
    )
    admin2_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": admin2_user, "password": "TestPass1234"},
    )
    admin2_headers = {"Authorization": f"Bearer {admin2_login.json()['data']['access_token']}"}

    resp = client.patch(
        f"/api/v1/users/{super_admin_id}",
        headers=admin2_headers,
        json={"real_name": "HACKED"},
    )
    # NOW correctly rejected
    assert resp.status_code == 400, (
        f"Expected PATCH real_name on super_admin to be rejected: {resp.text}"
    )
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


# ===========================================================================
# CLAIM 4: "Project soft-delete does not cascade to drawings"
# ===========================================================================


def test_drawing_accessible_after_project_soft_delete():
    """PROVE: drawings remain accessible after parent project is soft-deleted."""
    client = _client()
    headers = _login(client)

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("CASCTEST"), "name": "Cascade Test"},
    )
    project_id = project.json()["data"]["id"]

    drawing = client.post(
        "/api/v1/drawings",
        headers=headers,
        json={"project_id": project_id, "drawing_no": _unique("DWG-CASC")},
    )
    drawing_id = drawing.json()["data"]["id"]

    client.delete(f"/api/v1/projects/{project_id}", headers=headers)

    # Project is 404
    assert client.get(f"/api/v1/projects/{project_id}", headers=headers).status_code == 404

    # Drawing is NOW INACCESSIBLE — BUG-7: soft-deleted project cascades to drawings
    d = client.get(f"/api/v1/drawings/{drawing_id}", headers=headers)
    assert d.status_code == 404, f"Drawing of deleted project must return 404: {d.text}"


# ===========================================================================
# CLAIM 5: "DELETE /roles/{role_id} endpoint does not exist"
# ===========================================================================


def test_delete_role_endpoint_status():
    """Does DELETE /api/v1/roles/{role_id} exist?"""
    client = _client()
    headers = _login(client)

    # Try deleting a role — spec says this should exist but not defined in code
    resp = client.delete("/api/v1/roles/999", headers=headers)
    # Expected: either 404 (no route → Starlette 404) or 405 (method not allowed)
    assert resp.status_code in (404, 405), f"Unexpected: {resp.status_code} {resp.text}"


# ===========================================================================
# CLAIM 6: "Logout only revokes access token, refresh token still valid"
# ===========================================================================


def test_refresh_token_revoked_after_logout():
    """VERIFIED: logout now blacklists BOTH access AND refresh tokens.

    The ``delete_current_session`` handler reads the refresh token from the
    request cookie and calls ``blacklist_access_token()`` on it.  The refresh
    endpoint checks ``is_token_blacklisted(jti)`` and returns 401 TOKEN_REVOKED
    if the jti is found.

    This was my CLAIM 6 — I assumed it was broken, but the code proves me wrong.
    """
    client = _client()

    login = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    assert login.status_code == 201
    refresh_value = client.cookies.get("dwg_refresh_token")
    assert refresh_value, "No refresh cookie set"

    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    # Logout — blacklists both access + refresh tokens
    client.delete("/api/v1/auth/sessions/current", headers=headers)

    # Cookie cleared
    assert client.cookies.get("dwg_refresh_token") is None or client.cookies.get("dwg_refresh_token") == ""

    # Manually restore the old refresh cookie to simulate a pre-captured value
    client.cookies.set("dwg_refresh_token", refresh_value, path="/api/v1/auth")

    # Attempt to refresh with the revoked token
    resp = client.post("/api/v1/auth/tokens/refresh")
    # CORRECTLY rejected — the refresh token jti is in the Redis blacklist
    assert resp.status_code == 401, f"Expected 401 TOKEN_REVOKED, got: {resp.text}"
    assert resp.json()["error"]["code"] == "TOKEN_REVOKED"
    assert "Refresh token has been revoked" in resp.json()["error"]["message"]
