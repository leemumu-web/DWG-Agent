"""Tests for recently added functionality: atomic status transitions, self-service
password-reset guard, require_active_project integration, UserSelfUpdate schema."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.platform.database.seed import init_db


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _admin(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _create_user(
    client: TestClient,
    admin_headers: dict[str, str],
    username: str,
    password: str = "TestPass1234",
    real_name: str = "Test User",
    roles: list[str] | None = None,
) -> int:
    """Create a user and optionally assign roles. Returns user_id."""
    resp = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": real_name},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["data"]["id"]
    for role_code in (roles or []):
        r = client.post(
            f"/api/v1/users/{user_id}/roles",
            headers=admin_headers,
            json={"role_code": role_code},
        )
        assert r.status_code == 201, f"Failed to assign {role_code}: {r.text}"
    return user_id


# =============================================================================
# transition_user_status — atomic status transitions
# =============================================================================


def test_delete_then_delete_again_returns_404():
    """Second delete returns 404 — get_user_or_404 excludes deleted users."""
    client = _client()
    headers = _admin(client)

    uid = _create_user(client, headers, _unique("double-del"), roles=["viewer"])

    # First delete succeeds
    r1 = client.delete(f"/api/v1/users/{uid}", headers=headers)
    assert r1.status_code == 204

    # Second delete: get_user_or_404 returns 404 for deleted users
    r2 = client.delete(f"/api/v1/users/{uid}", headers=headers)
    assert r2.status_code == 404, r2.text


def test_disable_already_deleted_user_returns_404():
    """Disabling a deleted user returns 404 — get_user_or_404 blocks it."""
    client = _client()
    headers = _admin(client)

    uid = _create_user(client, headers, _unique("del-disable"), roles=["viewer"])
    client.delete(f"/api/v1/users/{uid}", headers=headers)

    r = client.post(f"/api/v1/users/{uid}/disable-requests", headers=headers)
    assert r.status_code == 404, r.text


def test_enable_already_deleted_user_returns_404():
    """Enabling a deleted user returns 404 — get_user_or_404 blocks it."""
    client = _client()
    headers = _admin(client)

    uid = _create_user(client, headers, _unique("del-enable"), roles=["viewer"])
    client.delete(f"/api/v1/users/{uid}", headers=headers)

    r = client.post(f"/api/v1/users/{uid}/enable-requests", headers=headers)
    assert r.status_code == 404, r.text


def test_disable_then_enable_uses_atomic_transition():
    """Disable and re-enable a user via the atomic transition_user_status."""
    client = _client()
    headers = _admin(client)

    username = _unique("atom-enable")
    uid = _create_user(client, headers, username, "TestPass1234", roles=["viewer"])

    # Disable
    r1 = client.post(f"/api/v1/users/{uid}/disable-requests", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["status"] == "disabled"

    # Enable
    r2 = client.post(f"/api/v1/users/{uid}/enable-requests", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["status"] == "active"

    # User can login again after enable
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": "TestPass1234"},
    )
    assert login.status_code == 201, login.text


# =============================================================================
# reset_user_password — self-service guard
# =============================================================================


def test_viewer_cannot_reset_another_users_password():
    """Non-admin users must be forbidden from resetting others' passwords."""
    client = _client()
    headers = _admin(client)

    viewer_user = _unique("pwr-viewer")
    _create_user(client, headers, viewer_user, "ViewerPass1234", roles=["viewer"])
    viewer_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": viewer_user, "password": "ViewerPass1234"},
    )
    viewer_headers = {
        "Authorization": f"Bearer {viewer_login.json()['data']['access_token']}"
    }

    target_id = _create_user(client, headers, _unique("pwr-target"), roles=["viewer"])

    r = client.post(
        f"/api/v1/users/{target_id}/password-reset-requests",
        headers=viewer_headers,
    )
    assert r.status_code == 403, r.text


def test_viewer_cannot_reset_own_password():
    """Self-service password reset is not yet implemented."""
    client = _client()
    headers = _admin(client)

    viewer_user = _unique("pwr-self")
    viewer_id = _create_user(
        client, headers, viewer_user, "ViewerPass1234", roles=["viewer"]
    )
    viewer_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": viewer_user, "password": "ViewerPass1234"},
    )
    viewer_headers = {
        "Authorization": f"Bearer {viewer_login.json()['data']['access_token']}"
    }

    r = client.post(
        f"/api/v1/users/{viewer_id}/password-reset-requests",
        headers=viewer_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "SELF_RESET_NOT_IMPLEMENTED"


def test_admin_can_reset_user_password():
    """Admin-initiated password reset must work and generate a valid temp password."""
    client = _client()
    headers = _admin(client)

    username = _unique("pwr-admin-reset")
    uid = _create_user(client, headers, username, "OldPassword123", roles=["viewer"])

    r = client.post(f"/api/v1/users/{uid}/password-reset-requests", headers=headers)
    assert r.status_code == 200, r.text
    temp_password = r.json()["data"]["temp_password"]
    assert len(temp_password) > 10  # f"temp-{uuid4().hex[:12]}"

    # Old password no longer works
    old = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": "OldPassword123"},
    )
    assert old.status_code == 401

    # Temp password works
    new = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": temp_password},
    )
    assert new.status_code == 201, new.text


# =============================================================================
# require_active_project inside require_project_member
# =============================================================================


def test_project_member_blocked_from_deleted_project_drawing():
    """After project deletion, member can no longer access its drawings (BUG-7)."""
    client = _client()
    headers = _admin(client)

    # Create project + drawing
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("CASCADE2"), "name": "Cascade v2"},
    )
    project_id = project.json()["data"]["id"]

    drawing = client.post(
        "/api/v1/drawings",
        headers=headers,
        json={"project_id": project_id, "drawing_no": _unique("DWG")},
    )
    drawing_id = drawing.json()["data"]["id"]

    # Create viewer member
    viewer_user = _unique("cascade-viewer")
    viewer_pass = "ViewerPass1234"
    viewer_id = _create_user(client, headers, viewer_user, viewer_pass, roles=["viewer"])
    client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )
    viewer_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": viewer_user, "password": viewer_pass},
    )
    viewer_headers = {
        "Authorization": f"Bearer {viewer_login.json()['data']['access_token']}"
    }

    # Viewer can access drawing while project active
    d1 = client.get(f"/api/v1/drawings/{drawing_id}", headers=viewer_headers)
    assert d1.status_code == 200

    # Delete project
    client.delete(f"/api/v1/projects/{project_id}", headers=headers)

    # Viewer can NO LONGER access drawing
    d2 = client.get(f"/api/v1/drawings/{drawing_id}", headers=viewer_headers)
    assert d2.status_code == 404, f"Expected 404, got {d2.status_code}: {d2.text}"


def test_admin_still_blocked_from_deleted_project_drawing():
    """Even global admins get 404 for drawings in deleted projects."""
    client = _client()
    headers = _admin(client)

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("ADMDEL"), "name": "Admin Delete"},
    )
    project_id = project.json()["data"]["id"]

    drawing = client.post(
        "/api/v1/drawings",
        headers=headers,
        json={"project_id": project_id, "drawing_no": _unique("DWG-ADM")},
    )
    drawing_id = drawing.json()["data"]["id"]

    # Delete
    client.delete(f"/api/v1/projects/{project_id}", headers=headers)

    # Admin can't access drawing of deleted project
    d = client.get(f"/api/v1/drawings/{drawing_id}", headers=headers)
    assert d.status_code == 404, d.text


# =============================================================================
# UserSelfUpdate schema
# =============================================================================


def test_user_self_update_ignores_status_field():
    """UserSelfUpdate silently ignores 'status' — not a field on the schema."""
    from app.schemas.user_schema import UserSelfUpdate

    u = UserSelfUpdate(real_name="New Name")
    assert u.real_name == "New Name"

    u2 = UserSelfUpdate(email="test@example.com")
    assert u2.email == "test@example.com"

    # status is not a field on UserSelfUpdate — Pydantic silently ignores it
    u3 = UserSelfUpdate(status="disabled")  # type: ignore[call-arg]
    assert not hasattr(u3, "status") or u3.model_dump().get("status") is None


def test_user_self_update_rejects_html_in_real_name():
    """UserSelfUpdate must reject HTML tags in real_name (BUG-3)."""
    from pydantic import ValidationError

    from app.schemas.user_schema import UserSelfUpdate

    try:
        UserSelfUpdate(real_name="<script>alert(1)</script>")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass

    # Clean real_name accepted
    u = UserSelfUpdate(real_name="Clean Name")
    assert u.real_name == "Clean Name"


# =============================================================================
# for_update=True — SELECT FOR UPDATE lock
# =============================================================================


def test_update_deleted_user_returns_404_via_for_update():
    """update_user_api uses for_update=True → deleted user returns 404 before lock."""
    client = _client()
    headers = _admin(client)

    uid = _create_user(client, headers, _unique("forupdate"), roles=["viewer"])
    client.delete(f"/api/v1/users/{uid}", headers=headers)

    r = client.patch(
        f"/api/v1/users/{uid}",
        headers=headers,
        json={"real_name": "Hacked"},
    )
    assert r.status_code == 404, r.text


# =============================================================================
# update_user_api with for_update
# =============================================================================


def test_admin_updating_deleted_user_returns_404():
    """PATCH on a deleted user must return 404 (for_update check)."""
    client = _client()
    headers = _admin(client)

    uid = _create_user(client, headers, _unique("patch-del"), roles=["viewer"])
    client.delete(f"/api/v1/users/{uid}", headers=headers)

    r = client.patch(
        f"/api/v1/users/{uid}",
        headers=headers,
        json={"real_name": "Should Not Work"},
    )
    assert r.status_code == 404, r.text


# =============================================================================
# delete_user_api — atomic transition + audit
# =============================================================================


def test_delete_user_audits_with_correct_user_id():
    """Delete audit log must reference the deleted user_id (not loaded user object)."""
    client = _client()
    headers = _admin(client)

    uid = _create_user(client, headers, _unique("audit-del"), roles=["viewer"])
    client.delete(f"/api/v1/users/{uid}", headers=headers)

    # Check audit log
    logs = client.get("/api/v1/audit-logs?page_size=50", headers=headers)
    assert logs.status_code == 200
    items = logs.json()["data"]
    delete_actions = [
        item for item in items
        if item["action"] == "users.delete" and item["resource_id"] == uid
    ]
    assert len(delete_actions) == 1, f"Expected 1 delete audit entry for user {uid}"


# =============================================================================
# Boundary: concurrent safety awareness
# =============================================================================


def test_delete_nonexistent_user_returns_404():
    """DELETE on non-existent user returns 404, not 500."""
    client = _client()
    headers = _admin(client)

    r = client.delete("/api/v1/users/99999", headers=headers)
    assert r.status_code == 404, r.text


# =============================================================================
# PATCH /users/me — self-update endpoint (BUG-12)
# =============================================================================


def test_user_can_update_own_real_name():
    """Any authenticated user can PATCH their own real_name."""
    client = _client()
    headers = _admin(client)

    viewer_user = _unique("selfupdate")
    viewer_pass = "ViewerPass1234"
    _create_user(client, headers, viewer_user, viewer_pass, roles=["viewer"])
    viewer_headers = {
        "Authorization": f"Bearer {client.post('/api/v1/auth/sessions', json={'username': viewer_user, 'password': viewer_pass}).json()['data']['access_token']}"
    }

    # Viewer updates own real_name
    r = client.patch(
        "/api/v1/users/me",
        headers=viewer_headers,
        json={"real_name": "Updated Name"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["real_name"] == "Updated Name"


def test_user_can_update_own_email():
    """Any authenticated user can PATCH their own email."""
    client = _client()
    headers = _admin(client)

    viewer_user = _unique("selfemail")
    viewer_pass = "ViewerPass1234"
    _create_user(client, headers, viewer_user, viewer_pass, roles=["viewer"])
    viewer_headers = {
        "Authorization": f"Bearer {client.post('/api/v1/auth/sessions', json={'username': viewer_user, 'password': viewer_pass}).json()['data']['access_token']}"
    }

    r = client.patch(
        "/api/v1/users/me",
        headers=viewer_headers,
        json={"email": "new-email@example.com"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["email"] == "new-email@example.com"


def test_user_cannot_change_own_status_via_me():
    """PATCH /users/me silently ignores status — not a field on UserSelfUpdate."""
    client = _client()
    headers = _admin(client)

    viewer_user = _unique("selfstatus")
    viewer_pass = "ViewerPass1234"
    _create_user(client, headers, viewer_user, viewer_pass, roles=["viewer"])
    viewer_headers = {
        "Authorization": f"Bearer {client.post('/api/v1/auth/sessions', json={'username': viewer_user, 'password': viewer_pass}).json()['data']['access_token']}"
    }

    r = client.patch(
        "/api/v1/users/me",
        headers=viewer_headers,
        json={"status": "disabled"},
    )
    # Accepted (status silently ignored) — status unchanged
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "active"


def test_self_update_rejects_html_in_real_name():
    """PATCH /users/me must reject HTML tags in real_name."""
    client = _client()
    headers = _admin(client)

    viewer_user = _unique("selfhtml")
    viewer_pass = "ViewerPass1234"
    _create_user(client, headers, viewer_user, viewer_pass, roles=["viewer"])
    viewer_headers = {
        "Authorization": f"Bearer {client.post('/api/v1/auth/sessions', json={'username': viewer_user, 'password': viewer_pass}).json()['data']['access_token']}"
    }

    r = client.patch(
        "/api/v1/users/me",
        headers=viewer_headers,
        json={"real_name": "<script>alert(1)</script>"},
    )
    assert r.status_code == 422, r.text


def test_unauthenticated_self_update_rejected():
    """PATCH /users/me without auth token returns 401."""
    client = _client()
    r = client.patch("/api/v1/users/me", json={"real_name": "No Auth"})
    assert r.status_code == 401, r.text
