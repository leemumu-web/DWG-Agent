"""Security boundary tests — try to break auth, RBAC, and access control.

Each test attempts a forbidden operation and expects a specific rejection.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.automation.agent.models.runs import AgentRun, AgentRunStep
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import AnalysisResult, Job


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
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["data"]["id"]
    for role_code in role_codes:
        role_resp = client.post(
            f"/api/v1/users/{user_id}/roles",
            headers=admin_headers,
            json={"role_code": role_code},
        )
        assert role_resp.status_code == 201, f"Failed to assign role {role_code}: {role_resp.text}"
    return user_id


# ---------------------------------------------------------------------------
# Super-admin protection: admin must not manage super_admin users
# ---------------------------------------------------------------------------


def test_admin_cannot_delete_super_admin():
    """An admin user cannot soft-delete a super_admin account."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    # Create a second admin to act as the attacker
    admin2_user = _unique("rogue-admin")
    admin2_pass = "RoguePass1234"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.delete(f"/api/v1/users/{super_admin_id}", headers=admin2_headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "SUPER_ADMIN_ACCOUNT_PROTECTED"


def test_admin_cannot_disable_super_admin():
    """An admin user cannot disable a super_admin account."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("rogue-admin2")
    admin2_pass = "RoguePass4567"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin 2", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.post(
        f"/api/v1/users/{super_admin_id}/disable-requests", headers=admin2_headers
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "SUPER_ADMIN_ACCOUNT_PROTECTED"


def test_admin_cannot_reset_super_admin_password():
    """An admin user cannot reset a super_admin password (account takeover)."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("rogue-admin3")
    admin2_pass = "RoguePass7890"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin 3", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.post(
        f"/api/v1/users/{super_admin_id}/password-reset-requests",
        headers=admin2_headers,
        json={"new_password": "DeniedSuperReset123"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "SUPER_ADMIN_ACCOUNT_PROTECTED"


def test_admin_can_apply_non_destructive_enable_to_super_admin():
    """Re-enabling is protective, so admin keeps the same ability as super_admin."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    me = client.get("/api/v1/auth/me", headers=admin_headers)
    super_admin_id = me.json()["data"]["id"]

    admin2_user = _unique("rogue-admin4")
    admin2_pass = "RoguePassAbc1"
    _create_user(client, admin_headers, admin2_user, admin2_pass, "Rogue Admin 4", ["admin"])
    admin2_headers = _login(client, admin2_user, admin2_pass)

    resp = client.post(
        f"/api/v1/users/{super_admin_id}/enable-requests", headers=admin2_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "active"


def test_super_admin_account_is_singleton_and_cannot_be_destroyed():
    """No second super_admin may be assigned and the sole account is indestructible."""
    client = _client()
    root_headers = _login(client, "admin", "SuperAdminPass1")
    protected = client.get("/api/v1/auth/me", headers=root_headers).json()["data"]
    protected_id = protected["id"]
    super_role_id = next(role["id"] for role in protected["roles"] if role["code"] == "super_admin")

    second_username = _unique("second-super")
    second_password = "SecondSuperPass1234"
    second_id = _create_user(
        client,
        root_headers,
        second_username,
        second_password,
        "Second Super Candidate",
        [],
    )
    singleton = client.post(
        f"/api/v1/users/{second_id}/roles",
        headers=root_headers,
        json={"role_code": "super_admin"},
    )
    assert singleton.status_code == 409, singleton.text
    assert singleton.json()["error"]["code"] == "SUPER_ADMIN_SINGLETON"

    attempts = (
        client.delete(f"/api/v1/users/{protected_id}", headers=root_headers),
        client.post(f"/api/v1/users/{protected_id}/disable-requests", headers=root_headers),
        client.patch(
            f"/api/v1/users/{protected_id}",
            headers=root_headers,
            json={"status": "disabled"},
        ),
        client.delete(
            f"/api/v1/users/{protected_id}/roles/{super_role_id}",
            headers=root_headers,
        ),
    )
    assert [response.status_code for response in attempts] == [400, 400, 400, 400]
    assert {response.json()["error"]["code"] for response in attempts} == {
        "SUPER_ADMIN_ACCOUNT_PROTECTED"
    }

    # The original credentials and sole privilege remain usable after every rejected attempt.
    me = client.get("/api/v1/auth/me", headers=_login(client, "admin", "SuperAdminPass1"))
    assert me.status_code == 200
    assert "super_admin" in {role["code"] for role in me.json()["data"]["roles"]}
    second = client.get(f"/api/v1/users/{second_id}", headers=root_headers).json()["data"]
    assert "super_admin" not in {role["code"] for role in second["roles"]}


def test_admin_can_update_non_destructive_super_admin_profile_fields():
    """Admin and super_admin stay equivalent outside the protected privilege boundary."""
    client = _client()
    root_headers = _login(client, "admin", "SuperAdminPass1")
    root_id = client.get("/api/v1/auth/me", headers=root_headers).json()["data"]["id"]

    admin_username = _unique("peer-admin")
    admin_password = "PeerAdminPass1234"
    _create_user(
        client,
        root_headers,
        admin_username,
        admin_password,
        "Peer Admin",
        ["admin"],
    )
    admin_headers = _login(client, admin_username, admin_password)

    updated = client.patch(
        f"/api/v1/users/{root_id}",
        headers=admin_headers,
        json={"real_name": "Super Admin Account"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["real_name"] == "Super Admin Account"


def test_super_admin_role_permissions_are_immutable():
    """Changing the built-in super_admin role must not become a privilege-kill bypass."""
    client = _client()
    root_headers = _login(client, "admin", "SuperAdminPass1")
    roles = client.get("/api/v1/roles?page_size=200", headers=root_headers).json()["data"]
    super_role_id = next(role["id"] for role in roles if role["code"] == "super_admin")

    response = client.put(
        f"/api/v1/roles/{super_role_id}/permissions",
        headers=root_headers,
        json={"permission_codes": []},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "SUPER_ADMIN_ROLE_PROTECTED"


# ---------------------------------------------------------------------------
# Token type enforcement
# ---------------------------------------------------------------------------


def test_refresh_token_cannot_be_used_as_access_token():
    """A refresh token (type=refresh) must not be accepted for API endpoints."""
    client = _client()

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
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
        json={"username": "admin", "password": "SuperAdminPass1"},
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    username = _unique("disable-refresh")
    password = "DisableMe12345"
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    username = _unique("disable-login")
    password = "DisableLogin123"
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    username = _unique("disable-mid-session")
    password = "MidSession1234"
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    # Admin uploads a file
    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("admin-file.dwg", BytesIO(b"AC1027" + b"X" * 1024), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    admin_file_id = upload.json()["data"]["id"]

    # Create viewer and have them upload a file
    viewer_user = _unique("file-list-viewer")
    viewer_pass = "ViewerPass1234"
    _create_user(client, admin_headers, viewer_user, viewer_pass, "File List Viewer", ["viewer"])
    viewer_headers = _login(client, viewer_user, viewer_pass)

    viewer_upload = client.post(
        "/api/v1/files",
        headers=viewer_headers,
        files={
            "upload": ("viewer-file.dwg", BytesIO(b"AC1027" + b"X" * 1024), "application/acad")
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


def test_file_list_access_control_has_constant_query_count(db: Session):
    """File visibility must be evaluated in SQL instead of one query per file."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    viewer_name = _unique("file-query-viewer")
    viewer_password = "ViewerQueryPass1234"
    viewer_id = _create_user(
        client,
        admin_headers,
        viewer_name,
        viewer_password,
        "File Query Viewer",
        ["viewer"],
    )
    viewer_headers = _login(client, viewer_name, viewer_password)

    for index in range(8):
        owner_id = viewer_id if index == 7 else 1
        db.add(
            StoredFile(
                bucket="dwg-original",
                storage_key=f"tests/access-query-{uuid4().hex}.dwg",
                original_name=f"access-query-{index}.dwg",
                file_ext=".dwg",
                content_type="application/acad",
                size_bytes=6,
                sha256=uuid4().hex + uuid4().hex,
                uploaded_by=owner_id,
                status="available",
            )
        )
    db.commit()

    access_queries: list[str] = []

    def record_access_query(_conn, _cursor, statement, _params, _context, _executemany):
        if "drawing_versions" in statement or "analysis_results" in statement:
            access_queries.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record_access_query)
    try:
        response = client.get("/api/v1/files?page_size=1", headers=viewer_headers)
    finally:
        event.remove(engine, "before_cursor_execute", record_access_query)

    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 1
    assert response.json()["data"][0]["uploaded_by"] == viewer_id
    assert len(access_queries) <= 2


def test_signed_download_url_expiry_is_enforced():
    """An expired signed download URL must be rejected."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("expire-test.dwg", BytesIO(b"AC1027" + b"X" * 1024), "application/acad")},
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("sig-test.dwg", BytesIO(b"AC1027" + b"X" * 1024), "application/acad")},
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    # Upload two files
    for name in ("file-a.dwg", "file-b.dwg"):
        resp = client.post(
            "/api/v1/files",
            headers=admin_headers,
            files={"upload": (name, BytesIO(b"AC1027" + b"X" * 1024), "application/acad")},
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
    _login(client, "admin", "SuperAdminPass1")  # ensures DB is seeded

    # Endpoints that MUST require auth (should return 401, not 403 or 500)
    protected_paths = [
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/roles"),
        ("POST", "/api/v1/roles"),
        ("GET", "/api/v1/workflows/projects"),
        ("POST", "/api/v1/workflows/projects"),
        ("GET", "/api/v1/files"),
        ("POST", "/api/v1/files"),
        ("GET", "/api/v1/workflows/drawings"),
        ("POST", "/api/v1/workflows/drawings"),
        ("GET", "/api/v1/workflows/jobs"),
        ("POST", "/api/v1/workflows/jobs"),
        ("GET", "/api/v1/audit-logs"),
        ("POST", "/api/v1/agent-runs"),
        ("GET", "/api/v1/agent-tools"),
        ("GET", "/api/v1/workflows/reviews/pending"),
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
    assert client.get("/health").status_code == 200

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
    headers = _login(client, "admin", "SuperAdminPass1")
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": "", "password": "TestPass1234", "real_name": "Empty"},
    )
    assert resp.status_code == 422, resp.text


def test_create_user_with_short_password_rejected():
    """Password shorter than 8 chars must be rejected by Pydantic validation."""
    client = _client()
    headers = _login(client, "admin", "SuperAdminPass1")
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
    headers = _login(client, "admin", "SuperAdminPass1")

    resp = client.patch(
        "/api/v1/auth/password",
        headers=headers,
        json={"current_password": "", "new_password": ""},
    )
    assert resp.status_code == 422, resp.text


def test_delete_already_deleted_user_returns_404():
    """Soft-deleting an already soft-deleted user returns 404 (not 500)."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    username = _unique("double-delete")
    user_id = _create_user(client, admin_headers, username, "TestPass1234", "DoubleDelete", ["viewer"])

    # First delete
    resp1 = client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert resp1.status_code == 204

    # Second delete
    resp2 = client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert resp2.status_code == 404, resp2.text


def test_get_nonexistent_resource_returns_404():
    """Accessing non-existent resource IDs returns 404 consistently."""
    client = _client()
    headers = _login(client, "admin", "SuperAdminPass1")

    paths = [
        ("GET", "/api/v1/users/99999"),
        ("GET", "/api/v1/workflows/projects/99999"),
        ("GET", "/api/v1/files/99999"),
        ("GET", "/api/v1/workflows/drawings/99999"),
        ("GET", "/api/v1/workflows/jobs/99999"),
        ("GET", "/api/v1/workflows/results/99999"),
        ("GET", "/api/v1/audit-logs/99999"),
    ]

    for method, path in paths:
        resp = client.get(path, headers=headers)
        assert resp.status_code == 404, f"{method} {path} returned {resp.status_code}: {resp.text}"
        assert resp.json()["error"]["code"] == "NOT_FOUND", f"{path} error code mismatch"

    # Agent endpoints return 503 when agent_enabled=false (stage 1), consistently
    # for all CRUD operations — 404 is only reachable when the agent is live.
    resp = client.get("/api/v1/agent-runs/99999", headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "AGENT_DISABLED"


def test_agent_run_details_and_steps_are_restricted_to_the_owner(
    db: Session, monkeypatch
):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_name = _unique("agent-owner")
    outsider_name = _unique("agent-outsider")
    owner_id = _create_user(
        client, admin_headers, owner_name, "AgentOwnerPass1", "Agent Owner", ["viewer"]
    )
    _create_user(
        client,
        admin_headers,
        outsider_name,
        "AgentOutsiderPass1",
        "Agent Outsider",
        ["viewer"],
    )
    run = AgentRun(
        session_id=_unique("agent-session"),
        user_id=owner_id,
        task="private agent task",
        status="running",
    )
    db.add(run)
    db.flush()
    db.add(
        AgentRunStep(
            agent_run_id=run.id,
            step_type="reasoning",
            title="private step",
            content="private content",
            status="succeeded",
        )
    )
    db.commit()
    run_id = run.id
    monkeypatch.setattr("app.modules.automation.agent.routes.settings.agent_enabled", True)

    owner_headers = _login(client, owner_name, "AgentOwnerPass1")
    assert client.get(f"/api/v1/agent-runs/{run_id}", headers=owner_headers).status_code == 200
    assert (
        client.get(f"/api/v1/agent-runs/{run_id}/steps", headers=owner_headers).status_code
        == 200
    )

    outsider_headers = _login(client, outsider_name, "AgentOutsiderPass1")
    details = client.get(f"/api/v1/agent-runs/{run_id}", headers=outsider_headers)
    steps = client.get(f"/api/v1/agent-runs/{run_id}/steps", headers=outsider_headers)

    assert details.status_code == 403, details.text
    assert steps.status_code == 403, steps.text


def test_unscoped_result_endpoints_are_restricted_to_job_creator(db: Session):
    """Result routes must not bypass creator checks for jobs without a project."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_name = _unique("result-owner")
    outsider_name = _unique("result-outsider")
    owner_id = _create_user(
        client, admin_headers, owner_name, "ResultOwnerPass1", "Result Owner", ["viewer"]
    )
    _create_user(
        client,
        admin_headers,
        outsider_name,
        "ResultOutsiderPass1",
        "Result Outsider",
        ["viewer"],
    )
    job = Job(
        created_by=owner_id,
        task_type="framework_smoke_test",
        precision_level="normal",
        status="succeeded",
        progress=100,
    )
    db.add(job)
    db.flush()
    result = AnalysisResult(
        job_id=job.id,
        result_type="framework_smoke_test",
        result_json={"private": True},
        status="succeeded",
    )
    db.add(result)
    db.commit()
    result_id = result.id

    owner_headers = _login(client, owner_name, "ResultOwnerPass1")
    assert client.get(f"/api/v1/workflows/results/{result_id}", headers=owner_headers).status_code == 200
    assert (
        client.get(f"/api/v1/workflows/results/{result_id}/reviews", headers=owner_headers).status_code
        == 200
    )

    outsider_headers = _login(client, outsider_name, "ResultOutsiderPass1")
    for path in (
        f"/api/v1/workflows/results/{result_id}",
        f"/api/v1/workflows/results/{result_id}/download-url",
        f"/api/v1/workflows/results/{result_id}/reviews",
    ):
        response = client.get(path, headers=outsider_headers)
        assert response.status_code == 403, response.text

    review = client.post(
        f"/api/v1/workflows/results/{result_id}/reviews",
        headers=outsider_headers,
        json={"decision": "approved", "comment": "must be rejected"},
    )
    assert review.status_code == 403, review.text


# ---------------------------------------------------------------------------
# Audit log: sensitive operations must be recorded
# ---------------------------------------------------------------------------


def test_user_lifecycle_operations_are_audited():
    """Create, disable, enable, password-reset must all produce audit entries."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    username = _unique("audit-lifecycle")
    user_id = _create_user(client, admin_headers, username, "Lifecycle12345", "Audit Lifecycle", ["viewer"])

    client.post(f"/api/v1/users/{user_id}/disable-requests", headers=admin_headers)
    client.post(f"/api/v1/users/{user_id}/enable-requests", headers=admin_headers)
    client.post(
        f"/api/v1/users/{user_id}/password-reset-requests",
        headers=admin_headers,
        json={"new_password": "LifecycleReset123"},
    )

    logs = client.get("/api/v1/audit-logs?page_size=50", headers=admin_headers)
    assert logs.status_code == 200, logs.text
    actions = {item["action"] for item in logs.json()["data"]}

    for expected in ("users.create", "users.disable", "users.enable", "users.password_reset"):
        assert expected in actions, f"Missing audit action: {expected}"


def test_login_and_logout_are_audited():
    """Login and logout must be recorded in audit log."""
    client = _client()
    headers = _login(client, "admin", "SuperAdminPass1")

    client.delete("/api/v1/auth/sessions/current", headers=headers)

    logs = client.get("/api/v1/audit-logs?page_size=50", headers=headers)
    # After logout, we have no cookie — use original headers which still have valid token
    # Wait, the token is valid (stateless JWT). Let me re-login to check audit.
    headers2 = _login(client, "admin", "SuperAdminPass1")
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
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    viewer_user = _unique("proj-viewer")
    viewer_pass = "ViewerPass1234"
    viewer_id = _create_user(
        client, admin_headers, viewer_user, viewer_pass, "Project Viewer", ["viewer"]
    )

    project = client.post(
        "/api/v1/workflows/projects",
        headers=admin_headers,
        json={"code": _unique("VIEWERPROJ"), "name": "Viewer Project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    # Add viewer to project
    client.post(
        f"/api/v1/workflows/projects/{project_id}/members",
        headers=admin_headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )

    viewer_headers = _login(client, viewer_user, viewer_pass)

    # Viewer tries to add a member
    target_user = _unique("target-member")
    target_id = _create_user(
        client, admin_headers, target_user, "TargetPass1234", "Target", ["viewer"]
    )

    resp = client.post(
        f"/api/v1/workflows/projects/{project_id}/members",
        headers=viewer_headers,
        json={"user_id": target_id, "project_role": "project_viewer"},
    )
    assert resp.status_code == 403, resp.text


def test_project_viewer_cannot_update_project():
    """A user with project_viewer role cannot modify project metadata."""
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")

    viewer_user = _unique("proj-viewer2")
    viewer_pass = "Viewer2Pass123"
    viewer_id = _create_user(
        client, admin_headers, viewer_user, viewer_pass, "Project Viewer 2", ["viewer"]
    )

    project = client.post(
        "/api/v1/workflows/projects",
        headers=admin_headers,
        json={"code": _unique("STATICPROJ"), "name": "Static Project"},
    )
    project_id = project.json()["data"]["id"]

    client.post(
        f"/api/v1/workflows/projects/{project_id}/members",
        headers=admin_headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )

    viewer_headers = _login(client, viewer_user, viewer_pass)
    resp = client.patch(
        f"/api/v1/workflows/projects/{project_id}",
        headers=viewer_headers,
        json={"name": "Viewer Should Not Write"},
    )
    assert resp.status_code == 403, resp.text
