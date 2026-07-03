"""Security boundary tests — try to break auth, RBAC, and access control.

Each test attempts a forbidden operation and expects a specific rejection.
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


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _create_user(
    client: TestClient,
    admin_headers: dict[str, str],
    username: str,
    password: str,
    real_name: str,
    role_codes: list[str],
) -> int:
    resp = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": username,
            "password": password,
            "real_name": real_name,
            "role_codes": role_codes,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


# ---------------------------------------------------------------------------
# Super-admin protection: admin must not manage super_admin users
# ---------------------------------------------------------------------------


def test_admin_cannot_delete_super_admin():
    """An admin user cannot soft-delete a super_admin account."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    # Create a second admin to act as the attacker
    admin2_user = _unique("rogue-admin")
    admin2_pass = "rogue-pass-123"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.delete(f"/api/v1/users/{super_admin_id}", headers=admin2_headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


def test_admin_cannot_disable_super_admin():
    """An admin user cannot disable a super_admin account."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("rogue-admin2")
    admin2_pass = "rogue-pass-456"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin 2", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.post(
        f"/api/v1/users/{super_admin_id}/disable-requests", headers=admin2_headers
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


def test_admin_cannot_reset_super_admin_password():
    """An admin user cannot reset a super_admin password (account takeover)."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("rogue-admin3")
    admin2_pass = "rogue-pass-789"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin 3", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.post(
        f"/api/v1/users/{super_admin_id}/password-reset-requests", headers=admin2_headers
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


def test_admin_cannot_enable_super_admin():
    """An admin user cannot re-enable a disabled super_admin account."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("rogue-admin4")
    admin2_pass = "rogue-pass-abc"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin 4", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.post(
        f"/api/v1/users/{super_admin_id}/enable-requests", headers=admin2_headers
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "CANNOT_MANAGE_SUPER_ADMIN"


# ---------------------------------------------------------------------------
# Token type enforcement
# ---------------------------------------------------------------------------


def test_refresh_token_cannot_be_used_as_access_token():
    """A refresh token (type=refresh) must not be accepted for API endpoints."""
    client = _client()

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "admin123456"},
    )
    assert login.status_code == 201, login.text

    # Extract refresh token from cookie
    refresh_token = client.cookies.get("dwg_refresh_token")
    assert refresh_token

    # Try to use refresh token as Bearer token
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


def test_access_token_cannot_refresh():
    """An access token (type=access) must not work on the refresh endpoint."""
    client = _client()

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "admin123456"},
    )
    access_token = login.json()["data"]["access_token"]

    # Remove refresh cookie, set access token as cookie
    client.cookies.clear()
    client.cookies.set("dwg_refresh_token", access_token)

    resp = client.post("/api/v1/auth/tokens/refresh")
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


def test_disabled_user_refresh_token_is_rejected():
    """After a user is disabled, their refresh token must be rejected."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    username = _unique("disable-refresh")
    password = "disable-me-123"
    user_id = _create_user(client, admin_headers, username, password, "Disable Test", ["viewer"])

    # Login to get refresh cookie
    user_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert user_login.status_code == 201

    # Admin disables the user
    resp = client.post(f"/api/v1/users/{user_id}/disable-requests", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    # Try to refresh the disabled user's token
    refresh = client.post("/api/v1/auth/tokens/refresh")
    assert refresh.status_code == 401, refresh.text
    assert refresh.json()["error"]["code"] == "USER_NOT_ACTIVE"


def test_disabled_user_cannot_login():
    """A disabled user must not be able to login."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    username = _unique("disable-login")
    password = "disable-login-123"
    user_id = _create_user(client, admin_headers, username, password, "Login Disable", ["viewer"])

    # First login should work
    ok_login = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert ok_login.status_code == 201, ok_login.text

    # Disable
    client.post(f"/api/v1/users/{user_id}/disable-requests", headers=admin_headers)

    # Second login should fail
    bad_login = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert bad_login.status_code == 401, bad_login.text
    assert bad_login.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_disabled_user_active_token_is_rejected():
    """A user with a valid access token must get 401 after being disabled."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    username = _unique("disable-mid-session")
    password = "mid-session-123"
    user_id = _create_user(
        client, admin_headers, username, password, "Mid-Session", ["viewer"]
    )

    user_headers = _login(client, username, password)
    # Verify token works
    me = client.get("/api/v1/auth/me", headers=user_headers)
    assert me.status_code == 200, me.text

    # Admin disables the user mid-session
    client.post(f"/api/v1/users/{user_id}/disable-requests", headers=admin_headers)

    # Existing token must now be rejected (checked per-request)
    me_after = client.get("/api/v1/auth/me", headers=user_headers)
    assert me_after.status_code == 401, me_after.text
    assert me_after.json()["error"]["code"] == "USER_NOT_ACTIVE"


# ---------------------------------------------------------------------------
# File access control — ownership, project membership, signed URLs
# ---------------------------------------------------------------------------


def test_viewer_cannot_see_unowned_files_in_list():
    """A viewer must not see files uploaded by other users in list_files."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    # Admin uploads a file
    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("admin-file.dwg", BytesIO(b"AC1027-ADMIN-FILE-STUB"), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    admin_file_id = upload.json()["data"]["id"]

    # Create viewer and have them upload a file
    viewer_user = _unique("file-list-viewer")
    viewer_pass = "viewer-pass-123"
    _create_user(client, admin_headers, viewer_user, viewer_pass, "File List Viewer", ["viewer"])
    viewer_headers = _login(client, viewer_user, viewer_pass)

    viewer_upload = client.post(
        "/api/v1/files",
        headers=viewer_headers,
        files={
            "upload": ("viewer-file.dwg", BytesIO(b"AC1027-VIEWER-FILE-STUB"), "application/acad")
        },
    )
    assert viewer_upload.status_code == 201, viewer_upload.text
    viewer_file_id = viewer_upload.json()["data"]["id"]

    # Viewer lists files — should only see their own
    listed = client.get("/api/v1/files", headers=viewer_headers)
    assert listed.status_code == 200, listed.text
    visible_ids = {item["id"] for item in listed.json()["data"]}
    assert viewer_file_id in visible_ids
    assert admin_file_id not in visible_ids


def test_signed_download_url_expiry_is_enforced():
    """An expired signed download URL must be rejected."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("expire-test.dwg", BytesIO(b"AC1027-EXPIRE-TEST"), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["data"]["id"]

    # Get a signed URL and tamper with expiry
    dl = client.get(f"/api/v1/files/{file_id}/download-url", headers=admin_headers)
    assert dl.status_code == 200, dl.text
    url = dl.json()["data"]["url"]

    # Replace expires with a past timestamp
    import re

    old_expires = re.search(r"expires=(\d+)", url).group(1)
    past = str(int(old_expires) - 99999)
    expired_url = url.replace(f"expires={old_expires}", f"expires={past}")

    resp = client.get(expired_url, headers=admin_headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "DOWNLOAD_URL_EXPIRED"


def test_signed_download_url_with_wrong_signature_rejected():
    """A download URL with a tampered signature must be rejected."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("sig-test.dwg", BytesIO(b"AC1027-SIG-TEST"), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["data"]["id"]

    dl = client.get(f"/api/v1/files/{file_id}/download-url", headers=admin_headers)
    url = dl.json()["data"]["url"]

    # Flip one hex digit in the signature
    tampered = url.replace("signature=", "signature=deadbeef")
    resp = client.get(tampered, headers=admin_headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "INVALID_DOWNLOAD_SIGNATURE"


def test_signed_url_for_wrong_file_rejected():
    """A signature valid for file A must not work for file B."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    # Upload two files
    for name in ("file-a.dwg", "file-b.dwg"):
        resp = client.post(
            "/api/v1/files",
            headers=admin_headers,
            files={"upload": (name, BytesIO(b"AC1027-DWG-TEST-STUB"), "application/acad")},
        )
        assert resp.status_code == 201, resp.text

    # Get signed URL for file A
    dl_a = client.get("/api/v1/files/1/download-url", headers=admin_headers)
    assert dl_a.status_code == 200, dl_a.text
    url_a = dl_a.json()["data"]["url"]

    # Try using file A's signature on file B's download path
    cross_url = url_a.replace("/files/1/download?", "/files/2/download?")
    resp = client.get(cross_url, headers=admin_headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "INVALID_DOWNLOAD_SIGNATURE"


# ---------------------------------------------------------------------------
# Unauthenticated access — every business endpoint must reject missing auth
# ---------------------------------------------------------------------------


def test_unauthenticated_endpoints_return_401():
    """All business API endpoints must return 401 when no token is provided."""
    client = _client()
    _login(client, "admin", "admin123456")  # ensures DB is seeded

    # Endpoints that MUST require auth (should return 401, not 403 or 500)
    protected_paths = [
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/roles"),
        ("POST", "/api/v1/roles"),
        ("GET", "/api/v1/projects"),
        ("POST", "/api/v1/projects"),
        ("GET", "/api/v1/files"),
        ("POST", "/api/v1/files"),
        ("GET", "/api/v1/drawings"),
        ("POST", "/api/v1/drawings"),
        ("GET", "/api/v1/jobs"),
        ("POST", "/api/v1/jobs"),
        ("GET", "/api/v1/audit-logs"),
        ("POST", "/api/v1/agent-runs"),
        ("GET", "/api/v1/agent-tools"),
        ("GET", "/api/v1/reviews/pending"),
    ]

    for method, path in protected_paths:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json={})
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}: {resp.text}"
        assert "error" in resp.json(), f"{method} {path} missing error envelope"


def test_public_endpoints_do_not_require_auth():
    """Health endpoints and login are intentionally public."""
    client = _client()

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200

    # Login with bad credentials returns 401 (not 403/500)
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": "nobody", "password": "wrong"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Input validation edge cases
# ---------------------------------------------------------------------------


def test_create_user_with_empty_username_rejected():
    """Empty username must be rejected by Pydantic validation (422)."""
    client = _client()
    headers = _login(client, "admin", "admin123456")
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": "", "password": "pass12345678", "real_name": "Empty"},
    )
    assert resp.status_code == 422, resp.text


def test_create_user_with_short_password_rejected():
    """Password shorter than 8 chars must be rejected by Pydantic validation."""
    client = _client()
    headers = _login(client, "admin", "admin123456")
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": _unique("short"), "password": "1234567", "real_name": "Short"},
    )
    assert resp.status_code == 422, resp.text


def test_login_with_empty_body_returns_422():
    """Missing credentials must be rejected with 422."""
    client = _client()
    resp = client.post("/api/v1/auth/sessions", json={})
    assert resp.status_code == 422, resp.text


def test_change_password_rejects_empty_fields():
    """Password change with empty fields must validate."""
    client = _client()
    headers = _login(client, "admin", "admin123456")

    resp = client.patch(
        "/api/v1/auth/password",
        headers=headers,
        json={"current_password": "", "new_password": ""},
    )
    assert resp.status_code == 422, resp.text


def test_delete_already_deleted_user_returns_404():
    """Soft-deleting an already soft-deleted user returns 404 (not 500)."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    username = _unique("double-delete")
    user_id = _create_user(client, admin_headers, username, "pass12345678", "DoubleDelete", ["viewer"])

    # First delete
    resp1 = client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert resp1.status_code == 204

    # Second delete
    resp2 = client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert resp2.status_code == 404, resp2.text


def test_get_nonexistent_resource_returns_404():
    """Accessing non-existent resource IDs returns 404 consistently."""
    client = _client()
    headers = _login(client, "admin", "admin123456")

    paths = [
        ("GET", "/api/v1/users/99999"),
        ("GET", "/api/v1/projects/99999"),
        ("GET", "/api/v1/files/99999"),
        ("GET", "/api/v1/drawings/99999"),
        ("GET", "/api/v1/jobs/99999"),
        ("GET", "/api/v1/results/99999"),
        ("GET", "/api/v1/audit-logs/99999"),
        ("GET", "/api/v1/agent-runs/99999"),
    ]

    for method, path in paths:
        resp = client.get(path, headers=headers)
        assert resp.status_code == 404, f"{method} {path} returned {resp.status_code}: {resp.text}"
        assert resp.json()["error"]["code"] == "NOT_FOUND", f"{path} error code mismatch"


# ---------------------------------------------------------------------------
# Audit log: sensitive operations must be recorded
# ---------------------------------------------------------------------------


def test_user_lifecycle_operations_are_audited():
    """Create, disable, enable, password-reset must all produce audit entries."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    username = _unique("audit-lifecycle")
    user_id = _create_user(client, admin_headers, username, "lifecycle123", "Audit Lifecycle", ["viewer"])

    client.post(f"/api/v1/users/{user_id}/disable-requests", headers=admin_headers)
    client.post(f"/api/v1/users/{user_id}/enable-requests", headers=admin_headers)
    client.post(f"/api/v1/users/{user_id}/password-reset-requests", headers=admin_headers)

    logs = client.get("/api/v1/audit-logs?page_size=50", headers=admin_headers)
    assert logs.status_code == 200, logs.text
    actions = {item["action"] for item in logs.json()["data"]}

    for expected in ("users.create", "users.disable", "users.enable", "users.password_reset"):
        assert expected in actions, f"Missing audit action: {expected}"


def test_login_and_logout_are_audited():
    """Login and logout must be recorded in audit log."""
    client = _client()
    headers = _login(client, "admin", "admin123456")

    client.delete("/api/v1/auth/sessions/current", headers=headers)

    logs = client.get("/api/v1/audit-logs?page_size=50", headers=headers)
    # After logout, we have no cookie — use original headers which still have valid token
    # Wait, the token is valid (stateless JWT). Let me re-login to check audit.
    headers2 = _login(client, "admin", "admin123456")
    logs = client.get("/api/v1/audit-logs?page_size=50", headers=headers2)
    actions = {item["action"] for item in logs.json()["data"]}
    assert "auth.login" in actions
    assert "auth.logout" in actions


# ---------------------------------------------------------------------------
# Project membership: viewer cannot escalate
# ---------------------------------------------------------------------------


def test_project_viewer_cannot_add_members():
    """A user with project_viewer role cannot add members to the project."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    viewer_user = _unique("proj-viewer")
    viewer_pass = "viewer-pass-123"
    viewer_id = _create_user(
        client, admin_headers, viewer_user, viewer_pass, "Project Viewer", ["viewer"]
    )

    project = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"code": _unique("VIEWERPROJ"), "name": "Viewer Project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    # Add viewer to project
    client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=admin_headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )

    viewer_headers = _login(client, viewer_user, viewer_pass)

    # Viewer tries to add a member
    target_user = _unique("target-member")
    target_id = _create_user(
        client, admin_headers, target_user, "target-pass-123", "Target", ["viewer"]
    )

    resp = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=viewer_headers,
        json={"user_id": target_id, "project_role": "project_viewer"},
    )
    assert resp.status_code == 403, resp.text


def test_project_viewer_cannot_update_project():
    """A user with project_viewer role cannot modify project metadata."""
    client = _client()
    admin_headers = _login(client, "admin", "admin123456")

    viewer_user = _unique("proj-viewer2")
    viewer_pass = "viewer2-pass-123"
    viewer_id = _create_user(
        client, admin_headers, viewer_user, viewer_pass, "Project Viewer 2", ["viewer"]
    )

    project = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"code": _unique("STATICPROJ"), "name": "Static Project"},
    )
    project_id = project.json()["data"]["id"]

    client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=admin_headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )

    viewer_headers = _login(client, viewer_user, viewer_pass)
    resp = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=viewer_headers,
        json={"name": "Viewer Should Not Write"},
    )
    assert resp.status_code == 403, resp.text
