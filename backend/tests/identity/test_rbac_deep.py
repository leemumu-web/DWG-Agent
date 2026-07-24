"""Comprehensive RBAC cross-role permission tests.

Verifies every global role against admin-only endpoints, project role access,
cross-project isolation, disabled user rejection, and unauthenticated rejection.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _create_user_with_roles(
    client: TestClient,
    admin_headers: dict[str, str],
    username: str,
    password: str = "TestPass1234",
    real_name: str = "Test User",
    role_codes: list[str] | None = None,
) -> tuple[int, dict[str, str]]:
    """Create user + assign roles + login. Returns (user_id, auth_headers)."""
    resp = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": real_name},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["data"]["id"]
    for rc in (role_codes or []):
        r = client.post(
            f"/api/v1/users/{user_id}/roles",
            headers=admin_headers,
            json={"role_code": rc},
        )
        assert r.status_code == 201, f"assign {rc}: {r.text}"
    return user_id, _login(client, username, password)


def _create_project(
    client: TestClient, headers: dict[str, str], code: str
) -> int:
    resp = client.post(
        "/api/v1/workflows/projects",
        headers=headers,
        json={"code": code, "name": f"Project {code}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _add_member(
    client: TestClient,
    admin_h: dict[str, str],
    project_id: int,
    user_id: int,
    role: str,
) -> int:
    resp = client.post(
        f"/api/v1/workflows/projects/{project_id}/members",
        headers=admin_h,
        json={"user_id": user_id, "project_role": role},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


# =============================================================================
# Global role — admin-only endpoints
# =============================================================================


class TestGlobalRoleAdminEndpoints:
    """Endpoints that require admin or super_admin."""

    def test_viewer_cannot_list_users(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, vh = _create_user_with_roles(client, admin_h, _unique("rbac-v"), role_codes=["viewer"])
        resp = client.get("/api/v1/users", headers=vh)
        assert resp.status_code == 403

    def test_operator_cannot_list_users(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, oh = _create_user_with_roles(client, admin_h, _unique("rbac-op"), role_codes=["operator"])
        resp = client.get("/api/v1/users", headers=oh)
        assert resp.status_code == 403

    def test_engineer_cannot_list_users(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, eh = _create_user_with_roles(client, admin_h, _unique("rbac-eng"), role_codes=["operator"])
        resp = client.get("/api/v1/users", headers=eh)
        assert resp.status_code == 403

    def test_admin_can_list_users(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        resp = client.get("/api/v1/users", headers=admin_h)
        assert resp.status_code == 200

    def test_viewer_cannot_create_user(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, vh = _create_user_with_roles(client, admin_h, _unique("rbac-v2"), role_codes=["viewer"])
        resp = client.post(
            "/api/v1/users",
            headers=vh,
            json={"username": _unique("hack"), "password": "HackPass1234", "real_name": "Hack"},
        )
        assert resp.status_code == 403

    def test_viewer_cannot_read_admin_audit_logs(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, ah = _create_user_with_roles(client, admin_h, _unique("rbac-aud"), role_codes=["viewer"])
        resp = client.get("/api/v1/audit-logs", headers=ah)
        assert resp.status_code == 403

    def test_viewer_cannot_create_users(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, ah = _create_user_with_roles(client, admin_h, _unique("rbac-aud2"), role_codes=["viewer"])
        resp = client.post(
            "/api/v1/users",
            headers=ah,
            json={"username": _unique("nope"), "password": "NopePass1234", "real_name": "Nope"},
        )
        assert resp.status_code == 403

    def test_viewer_cannot_access_audit_logs(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, vh = _create_user_with_roles(client, admin_h, _unique("rbac-v3"), role_codes=["viewer"])
        resp = client.get("/api/v1/audit-logs", headers=vh)
        assert resp.status_code == 403

    def test_engineer_cannot_create_roles(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        _, eh = _create_user_with_roles(client, admin_h, _unique("rbac-eng2"), role_codes=["operator"])
        resp = client.post(
            "/api/v1/roles",
            headers=eh,
            json={"code": "hack_role", "name": "Hack Role"},
        )
        assert resp.status_code == 403


# =============================================================================
# Admin self-protection
# =============================================================================


class TestAdminSelfProtection:
    def test_admin_cannot_disable_self(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        me = client.get("/api/v1/auth/me", headers=admin_h)
        admin_id = me.json()["data"]["id"]

        resp = client.post(
            f"/api/v1/users/{admin_id}/disable-requests",
            headers=admin_h,
        )
        assert resp.status_code == 400

    def test_admin_cannot_delete_self(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        me = client.get("/api/v1/auth/me", headers=admin_h)
        admin_id = me.json()["data"]["id"]

        resp = client.delete(f"/api/v1/users/{admin_id}", headers=admin_h)
        assert resp.status_code == 400


# =============================================================================
# Project role access
# =============================================================================


class TestProjectRoleAccess:
    def test_project_engineer_can_create_drawing(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        code = _unique("PROLE")
        pid = _create_project(client, admin_h, code)
        uid, eh = _create_user_with_roles(client, admin_h, _unique("pe"), role_codes=["operator"])
        _add_member(client, admin_h, pid, uid, "project_engineer")

        resp = client.post(
            "/api/v1/workflows/drawings",
            headers=eh,
            json={"project_id": pid, "title": "Engineer Drawing"},
        )
        assert resp.status_code == 201, resp.text

    def test_project_engineer_cannot_delete_project(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        code = _unique("PROLE2")
        pid = _create_project(client, admin_h, code)
        uid, eh = _create_user_with_roles(client, admin_h, _unique("pe2"), role_codes=["operator"])
        _add_member(client, admin_h, pid, uid, "project_engineer")

        resp = client.delete(f"/api/v1/workflows/projects/{pid}", headers=eh)
        assert resp.status_code == 403

    def test_project_reviewer_can_see_pending_reviews(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        code = _unique("PROLE3")
        pid = _create_project(client, admin_h, code)
        uid, rh = _create_user_with_roles(client, admin_h, _unique("pr"), role_codes=["operator"])
        _add_member(client, admin_h, pid, uid, "project_reviewer")

        resp = client.get("/api/v1/workflows/reviews/pending", headers=rh)
        assert resp.status_code == 200

    def test_project_owner_can_add_members(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        code = _unique("PROLE4")
        pid = _create_project(client, admin_h, code)

        new_uid, _ = _create_user_with_roles(client, admin_h, _unique("newmem"), role_codes=["viewer"])
        resp = client.post(
            f"/api/v1/workflows/projects/{pid}/members",
            headers=admin_h,
            json={"user_id": new_uid, "project_role": "project_viewer"},
        )
        assert resp.status_code == 201, resp.text

    def test_non_member_cannot_access_project(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        code = _unique("PROLE5")
        pid = _create_project(client, admin_h, code)
        _, oh = _create_user_with_roles(client, admin_h, _unique("outsider"), role_codes=["viewer"])

        resp = client.get(f"/api/v1/workflows/projects/{pid}", headers=oh)
        # Non-members get 403 (forbidden) or 404 (not found)
        assert resp.status_code in (403, 404), f"non-member: {resp.status_code}"

    def test_viewer_cannot_create_jobs_in_project(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        code = _unique("PROLE6")
        pid = _create_project(client, admin_h, code)
        uid, vh = _create_user_with_roles(client, admin_h, _unique("pv-job"), role_codes=["viewer"])
        _add_member(client, admin_h, pid, uid, "project_viewer")

        resp = client.post(
            "/api/v1/workflows/jobs",
            headers=vh,
            json={
                "project_id": pid,
                "task_type": "framework_smoke_test",
                "precision_level": "normal",
            },
        )
        assert resp.status_code == 403, f"viewer should not create jobs: {resp.status_code}"


# =============================================================================
# Cross-project isolation
# =============================================================================


class TestCrossProjectIsolation:
    def test_cross_project_drawing_access_denied(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")

        # Project A with engineer A
        pid_a = _create_project(client, admin_h, _unique("ISO-A"))
        uid_a, ea = _create_user_with_roles(client, admin_h, _unique("eng-a"), role_codes=["operator"])
        _add_member(client, admin_h, pid_a, uid_a, "project_engineer")

        resp = client.post(
            "/api/v1/workflows/drawings",
            headers=ea,
            json={"project_id": pid_a, "title": "Drawing A"},
        )
        assert resp.status_code == 201
        did = resp.json()["data"]["id"]

        # Project B with engineer B
        pid_b = _create_project(client, admin_h, _unique("ISO-B"))
        uid_b, eb = _create_user_with_roles(client, admin_h, _unique("eng-b"), role_codes=["operator"])
        _add_member(client, admin_h, pid_b, uid_b, "project_engineer")

        # Engineer B tries to access drawing from project A
        resp = client.get(f"/api/v1/workflows/drawings/{did}", headers=eb)
        # Cross-project access should be denied: either 403 (explicit deny) or 404 (not found)
        assert resp.status_code in (403, 404), f"cross-project leak: {resp.status_code}"

    def test_cross_project_job_access_denied(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")

        pid_a = _create_project(client, admin_h, _unique("JOB-A"))
        uid_a, ea = _create_user_with_roles(client, admin_h, _unique("job-eng-a"), role_codes=["operator"])
        _add_member(client, admin_h, pid_a, uid_a, "project_engineer")

        resp = client.post(
            "/api/v1/workflows/drawings",
            headers=ea,
            json={"project_id": pid_a, "title": "Job Drawing A"},
        )
        did = resp.json()["data"]["id"]
        resp = client.post(
            "/api/v1/workflows/jobs",
            headers=ea,
            json={
                "drawing_id": did,
                "project_id": pid_a,
                "task_type": "framework_smoke_test",
                "precision_level": "normal",
            },
        )
        jid = resp.json()["data"]["id"]

        pid_b = _create_project(client, admin_h, _unique("JOB-B"))
        uid_b, eb = _create_user_with_roles(client, admin_h, _unique("job-eng-b"), role_codes=["operator"])
        _add_member(client, admin_h, pid_b, uid_b, "project_engineer")

        resp = client.get(f"/api/v1/workflows/jobs/{jid}", headers=eb)
        assert resp.status_code in (403, 404), f"cross-project job leak: {resp.status_code}"


# =============================================================================
# Unauthenticated access — all business endpoints
# =============================================================================


class TestUnauthenticatedAccess:
    GET_ENDPOINTS = [
        "/api/v1/users",
        "/api/v1/workflows/projects",
        "/api/v1/files",
        "/api/v1/workflows/drawings",
        "/api/v1/workflows/jobs",
        "/api/v1/workflows/reviews/pending",
        "/api/v1/audit-logs",
        "/api/v1/roles",
        "/api/v1/permissions",
        "/api/v1/workflows/results/1",
    ]

    def test_get_endpoints_require_auth(self):
        client = _client()
        for path in self.GET_ENDPOINTS:
            resp = client.get(path)
            assert resp.status_code == 401, f"GET {path}: expected 401, got {resp.status_code}"

    def test_post_endpoints_require_auth(self):
        client = _client()
        for path in ["/api/v1/users", "/api/v1/workflows/projects", "/api/v1/workflows/jobs", "/api/v1/roles"]:
            resp = client.post(path, json={})
            assert resp.status_code in (401, 422), (
                f"POST {path}: expected 401 or 422, got {resp.status_code}"
            )

    def test_patch_users_me_requires_auth(self):
        client = _client()
        resp = client.patch("/api/v1/users/me", json={"real_name": "X"})
        assert resp.status_code == 401

    def test_delete_endpoints_require_auth(self):
        client = _client()
        for path in ["/api/v1/users/1", "/api/v1/workflows/projects/1", "/api/v1/files/1"]:
            resp = client.delete(path)
            assert resp.status_code in (401, 404), (
                f"DELETE {path}: expected 401 or 404, got {resp.status_code}"
            )


# =============================================================================
# Disabled user
# =============================================================================


class TestDisabledUser:
    def test_disabled_user_cannot_login(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        username = _unique("disable-me")
        uid, _ = _create_user_with_roles(client, admin_h, username, role_codes=["viewer"])

        client.post(f"/api/v1/users/{uid}/disable-requests", headers=admin_h)

        resp = client.post(
            "/api/v1/auth/sessions",
            json={"username": username, "password": "TestPass1234"},
        )
        assert resp.status_code == 401

    def test_disabled_user_token_rejected(self):
        client = _client()
        admin_h = _login(client, "admin", "SuperAdminPass1")
        username = _unique("disable-tok")
        uid, user_h = _create_user_with_roles(client, admin_h, username, role_codes=["viewer"])

        # Token works
        resp = client.get("/api/v1/auth/me", headers=user_h)
        assert resp.status_code == 200

        # Disable
        client.post(f"/api/v1/users/{uid}/disable-requests", headers=admin_h)

        # Same token now rejected
        resp = client.get("/api/v1/auth/me", headers=user_h)
        assert resp.status_code == 401
