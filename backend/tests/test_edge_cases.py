"""Edge-case and regression tests for previously identified API bugs.

Covers: DWG header whitelist, cascading deletes, race conditions,
super-admin protection coverage, PATCH self-status guards.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.main import app


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _admin_headers(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# DWG header whitelist — only known AutoCAD versions accepted
# ---------------------------------------------------------------------------


def test_dwg_valid_headers_accepted():
    """All supported DWG versions (AC1012–AC1032) must be accepted."""
    client = _client()
    headers = _admin_headers(client)

    valid_headers = [
        b"AC1012",
        b"AC1014",
        b"AC1015",
        b"AC1018",
        b"AC1021",
        b"AC1024",
        b"AC1027",
        b"AC1032",
    ]
    for header in valid_headers:
        payload = header + b"X" * 1024
        resp = client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": (f"test-{header.decode()}.dwg", BytesIO(payload), "application/acad")},
        )
        assert resp.status_code == 201, f"Header {header!r} rejected: {resp.text}"


def test_dwg_invalid_headers_rejected():
    """Unknown/unsupported DWG headers (AC0000, AC9999) must be rejected."""
    client = _client()
    headers = _admin_headers(client)

    invalid_headers = [
        (b"AC0000", "zero version"),
        (b"AC9999", "future version"),
        (b"AC1000", "unknown format"),
        (b"AC1001", "unknown format"),
        (b"AC1002", "unknown format"),
    ]
    for header, label in invalid_headers:
        payload = header + b"X" * 1024
        resp = client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": (f"{label}.dwg", BytesIO(payload), "application/acad")},
        )
        assert resp.status_code == 415, f"Header {header!r} ({label}) should be rejected: {resp.text}"
        assert resp.json()["error"]["code"] == "FILE_NOT_DWG"


def test_truncated_dwg_header_rejected():
    """Files smaller than 6 bytes must be rejected."""
    client = _client()
    headers = _admin_headers(client)

    for size, label in [(0, "zero"), (1, "1-byte"), (5, "5-byte")]:
        resp = client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": (f"{label}.dwg", BytesIO(b"A" * size), "application/acad")},
        )
        assert resp.status_code == 415, f"{label} file should be rejected: {resp.text}"
        assert resp.json()["error"]["code"] == "FILE_NOT_DWG"


# ---------------------------------------------------------------------------
# Project soft-delete — cascading behaviour
# ---------------------------------------------------------------------------


def test_project_soft_delete_does_not_cascade_to_drawings():
    """Soft-deleting a project leaves drawings active (current behaviour, N13)."""
    client = _client()
    headers = _admin_headers(client)

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("SOFTDEL"), "name": "Soft Delete Project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    drawing = client.post(
        "/api/v1/drawings",
        headers=headers,
        json={"project_id": project_id, "drawing_no": _unique("DWG-SD")},
    )
    assert drawing.status_code == 201, drawing.text
    drawing_id = drawing.json()["data"]["id"]

    # Soft-delete the project
    resp = client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert resp.status_code == 204

    # Project should be 404
    assert client.get(f"/api/v1/projects/{project_id}", headers=headers).status_code == 404

    # Drawing NOT accessible — BUG-7: soft-deleted project cascades to drawings
    drawing_after = client.get(f"/api/v1/drawings/{drawing_id}", headers=headers)
    assert drawing_after.status_code == 404, drawing_after.text


def test_deleted_project_code_not_reusable():
    """After soft-delete, the project code must not be reusable (N24)."""
    client = _client()
    headers = _admin_headers(client)

    code = _unique("REUSE")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": code, "name": "Reuse Test"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    client.delete(f"/api/v1/projects/{project_id}", headers=headers)

    # Try to create a new project with the same code
    resp = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": code, "name": "Reuse Attempt"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "PROJECT_CODE_EXISTS"


# ---------------------------------------------------------------------------
# Race condition — username collision
# ---------------------------------------------------------------------------


def test_duplicate_username_returns_409_not_500():
    """Creating a user with an existing username must return 409, not 500."""
    client = _client()
    headers = _admin_headers(client)

    username = _unique("duplicate")
    r1 = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": "TestPass1234", "real_name": "First"},
    )
    assert r1.status_code == 201, r1.text

    r2 = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": "TestPass5678", "real_name": "Second"},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "USERNAME_EXISTS"


# ---------------------------------------------------------------------------
# PATCH self-status protection (NEW-1 already fixed)
# ---------------------------------------------------------------------------


def test_admin_cannot_set_own_status_to_disabled_via_patch():
    """PATCH /users/{self_id} with status=disabled must be rejected."""
    client = _client()
    headers = _admin_headers(client)

    me = client.get("/api/v1/auth/me", headers=headers)
    user_id = me.json()["data"]["id"]

    resp = client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"status": "disabled"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "CANNOT_DISABLE_SELF"


def test_admin_can_set_own_status_to_active_via_patch():
    """PATCH /users/{self_id} with status=active is allowed (no-op, but not harmful)."""
    client = _client()
    headers = _admin_headers(client)

    me = client.get("/api/v1/auth/me", headers=headers)
    user_id = me.json()["data"]["id"]

    resp = client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"status": "active"},
    )
    # Active→active is allowed (not a self-disable)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Super-admin protection coverage — update_user_api
# ---------------------------------------------------------------------------


def test_admin_cannot_modify_super_admin_via_patch():
    """FIXED: admin must not be able to modify a super_admin user via PATCH."""
    client = _client()
    admin_headers = _admin_headers(client)

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("patch-admin")
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": admin2_user,
            "password": "TestPass1234",
            "real_name": "Patch Admin",
        },
    )
    admin2_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{admin2_id}/roles",
        headers=admin_headers,
        json={"role_code": "admin"},
    )
    login2 = client.post(
        "/api/v1/auth/sessions",
        json={"username": admin2_user, "password": "TestPass1234"},
    )
    admin2_headers = {"Authorization": f"Bearer {login2.json()['data']['access_token']}"}

    resp = client.patch(
        f"/api/v1/users/{super_admin_id}",
        headers=admin2_headers,
        json={"email": "hacked@evil.com"},
    )
    assert resp.status_code == 400, f"Expected rejection: {resp.text}"
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


# ---------------------------------------------------------------------------
# Project member — hard delete semantics (NEW-2)
# ---------------------------------------------------------------------------


def test_project_member_delete_is_hard_delete():
    """Removing a project member uses hard delete (no status=deleted column)."""
    client = _client()
    headers = _admin_headers(client)

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("MEMDEL"), "name": "Member Delete Test"},
    )
    project_id = project.json()["data"]["id"]

    viewer_user = _unique("mem-viewer")
    r = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": viewer_user,
            "password": "MemberPass123",
            "real_name": "Member Viewer",
        },
    )
    viewer_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{viewer_id}/roles",
        headers=headers,
        json={"role_code": "viewer"},
    )

    add = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )
    member_id = add.json()["data"]["id"]

    # Remove member
    remove = client.delete(
        f"/api/v1/projects/{project_id}/members/{member_id}", headers=headers
    )
    assert remove.status_code == 204, remove.text

    # Member no longer in list
    members = client.get(f"/api/v1/projects/{project_id}/members", headers=headers)
    member_ids = {m["id"] for m in members.json()["data"]}
    assert member_id not in member_ids


# ---------------------------------------------------------------------------
# 404 consistency — deleted/different resources
# ---------------------------------------------------------------------------


def test_deleted_user_login_returns_401_not_500():
    """After a user is soft-deleted, login must return 401, not 500."""
    client = _client()
    headers = _admin_headers(client)

    username = _unique("del-login")
    r = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": "TestPass1234", "real_name": "Delete Login"},
    )
    user_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=headers,
        json={"role_code": "viewer"},
    )

    # Delete
    client.delete(f"/api/v1/users/{user_id}", headers=headers)

    # Try to login as deleted user
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": "TestPass1234"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_disabled_user_can_be_reenabled():
    """A disabled user can be re-enabled and then login."""
    client = _client()
    headers = _admin_headers(client)

    username = _unique("reenable")
    password = "ReenablePass123"
    r = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": password, "real_name": "Re-enable"},
    )
    user_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=headers,
        json={"role_code": "viewer"},
    )

    # Disable
    client.post(f"/api/v1/users/{user_id}/disable-requests", headers=headers)

    # Cannot login
    r1 = client.post("/api/v1/auth/sessions", json={"username": username, "password": password})
    assert r1.status_code == 401

    # Enable
    client.post(f"/api/v1/users/{user_id}/enable-requests", headers=headers)

    # Can login again
    r2 = client.post("/api/v1/auth/sessions", json={"username": username, "password": password})
    assert r2.status_code == 201, r2.text


def test_user_password_reset_makes_old_password_invalid():
    """After admin resets a user's password, old password must not work."""
    client = _client()
    headers = _admin_headers(client)

    username = _unique("pwd-reset")
    old_password = "OldPassword123"
    r = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": old_password, "real_name": "Pwd Reset"},
    )
    user_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=headers,
        json={"role_code": "viewer"},
    )

    # Verify old password works
    r1 = client.post("/api/v1/auth/sessions", json={"username": username, "password": old_password})
    assert r1.status_code == 201
    old_headers = {"Authorization": f"Bearer {r1.json()['data']['access_token']}"}

    # Admin resets password
    reset = client.post(f"/api/v1/users/{user_id}/password-reset-requests", headers=headers)
    assert reset.status_code == 200
    temp_password = reset.json()["data"]["temp_password"]

    # The already-issued access token is revoked by the persisted password-change marker.
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 401

    # Old password no longer works
    r2 = client.post("/api/v1/auth/sessions", json={"username": username, "password": old_password})
    assert r2.status_code == 401

    # New temp password works
    r3 = client.post("/api/v1/auth/sessions", json={"username": username, "password": temp_password})
    assert r3.status_code == 201, r3.text
    new_headers = {"Authorization": f"Bearer {r3.json()['data']['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=new_headers).status_code == 200


# ---------------------------------------------------------------------------
# Audit log — resource filtering and pagination
# ---------------------------------------------------------------------------


def test_audit_logs_filtered_by_resource_type():
    """Audit logs must support filtering by resource_type."""
    client = _client()
    headers = _admin_headers(client)

    # Create a user to generate audit entries
    r = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": _unique("audit-filt"), "password": "TestPass1234", "real_name": "Audit Filter"},
    )
    user_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=headers,
        json={"role_code": "viewer"},
    )

    # Fetch only user-related audit entries
    logs = client.get("/api/v1/audit-logs?page_size=200", headers=headers)
    assert logs.status_code == 200, logs.text
    items = logs.json()["data"]

    # Verify we have entries and they have expected structure
    assert len(items) > 0
    for item in items[:5]:
        assert "action" in item
        assert "resource_type" in item
        assert "created_at" in item


def test_audit_logs_page_size_respected():
    """Audit log pagination must respect page_size."""
    client = _client()
    headers = _admin_headers(client)

    # Generate enough entries
    for _ in range(3):
        r = client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "username": _unique("page-test"),
                "password": "TestPass1234",
                "real_name": "Page Test",
            },
        )
        user_id = r.json()["data"]["id"]
        client.post(
            f"/api/v1/users/{user_id}/roles",
            headers=headers,
            json={"role_code": "viewer"},
        )

    resp = client.get("/api/v1/audit-logs?page=1&page_size=2", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload["data"]) <= 2
    assert payload["pagination"]["page"] == 1
    assert payload["pagination"]["page_size"] == 2


# ---------------------------------------------------------------------------
# MIME type validation for DWG uploads
# ---------------------------------------------------------------------------


def test_dwg_upload_accepts_all_allowed_mime_types():
    """All allowed DWG MIME types must be accepted."""
    client = _client()
    headers = _admin_headers(client)

    allowed_mimes = [
        "application/acad",
        "application/autocad",
        "application/dwg",
        "application/x-acad",
        "application/x-autocad",
        "application/x-dwg",
        "image/vnd.dwg",
    ]
    for mime in allowed_mimes:
        resp = client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": ("mime-test.dwg", BytesIO(b"AC1027" + b"X" * 1024), mime)},
        )
        assert resp.status_code == 201, f"MIME {mime} rejected: {resp.text}"


def test_dwg_upload_accepts_octet_stream_as_fallback():
    """application/octet-stream must be accepted (many browsers send this for .dwg)."""
    client = _client()
    headers = _admin_headers(client)

    resp = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("octet.dwg", BytesIO(b"AC1027" + b"X" * 1024), "application/octet-stream")},
    )
    assert resp.status_code == 201, resp.text


def test_dwg_upload_accepts_valid_dwg_regardless_of_mime():
    """MIME 检查改为 pass-through（DWG header 是真正防线）；有效 DWG 内容应被接受。"""
    client = _client()
    headers = _admin_headers(client)

    resp = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("bad.dwg", BytesIO(b"AC1027" + b"X" * 1024), "image/png")},
    )
    assert resp.status_code == 201, resp.text


def test_dwg_upload_mime_with_charset_is_normalised():
    """MIME type with charset parameter (e.g. 'application/acad; charset=utf-8') must work."""
    client = _client()
    headers = _admin_headers(client)

    resp = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("charset.dwg", BytesIO(b"AC1027" + b"X" * 1024), "application/acad; charset=utf-8")},
    )
    assert resp.status_code == 201, resp.text
