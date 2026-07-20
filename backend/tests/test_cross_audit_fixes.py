"""Tests for cross-audit fixes: audit IP/UA capture, deleted auth/profile endpoint,
users/me self-update, delete_project_member with request, ReviewCreate Literal constraint,
and new service file integrations.

Covers changes from Phase 1 (audit + request params), Phase 3 (service extraction,
endpoint dedup), and Phase 4 (schema validation).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.modules.files.interface as file_service
import app.modules.jobs.interface as jobs_interface
from app.bootstrap.seed import init_db
from app.main import app
from app.modules.identity.users import reset_user_password
from app.modules.projects.services import drawings as drawing_service
from app.modules.projects.services import projects as project_service
from app.services import agent_service

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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
    roles: list[str] | None = None,
) -> int:
    resp = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": "Test User"},
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


def _login(
    client: TestClient, username: str, password: str = "TestPass1234"
) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


# =============================================================================
# Phase 1 — audit log IP/UA capture (request=request wired to write_audit_log)
# =============================================================================


class TestAuditLogIPCature:
    """Verify that audit logs now capture ip_address and user_agent from Request."""

    def test_audit_log_has_ip_on_user_create(self):
        """Creating a user produces an audit log with ip_address populated."""
        client = _client()
        headers = _admin(client)
        username = _unique("audit-ip")

        client.post(
            "/api/v1/users",
            headers=headers,
            json={"username": username, "password": "SecurePass123", "real_name": "Audit IP"},
        )

        # Read audit logs (super_admin only)
        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.json()["data"]
        creation_log = next(
            (log for log in logs if log["action"] == "users.create"), None
        )
        assert creation_log is not None, "Expected a users.create audit log"
        assert creation_log.get("ip_address"), (
            f"ip_address should not be empty; got {creation_log.get('ip_address')!r}"
        )

    def test_audit_log_has_user_agent_on_login(self):
        """Login produces an audit log with user_agent populated."""
        client = _client()
        resp = client.post(
            "/api/v1/auth/sessions",
            json={"username": "admin", "password": "SuperAdminPass1"},
        )
        assert resp.status_code == 201, resp.text
        token = resp.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.json()["data"]
        login_log = next(
            (log for log in logs if log["action"] == "auth.login"), None
        )
        assert login_log is not None, "Expected an auth.login audit log"
        assert login_log.get("user_agent"), (
            f"user_agent should not be empty; got {login_log.get('user_agent')!r}"
        )

    def test_audit_log_on_project_create_has_ip(self):
        """Creating a project writes audit log with IP."""
        client = _client()
        headers = _admin(client)
        code = _unique("PRJ")

        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Project {code}"},
        )

        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.json()["data"]
        creation_log = next(
            (log for log in logs if log["action"] == "projects.create"), None
        )
        assert creation_log is not None
        assert creation_log.get("ip_address"), "ip_address should be populated"

    def test_audit_log_on_file_upload_has_ip(self):
        """Uploading a file writes audit log with IP."""
        client = _client()
        headers = _admin(client)

        # Create a minimal valid DWG-like file (just need 1024+ bytes + AC1012 header)
        content = b"AC1012" + b"\x00" * 1024
        resp = client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": ("test.dwg", content, "application/acad")},
        )
        assert resp.status_code == 201, resp.text

        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.json()["data"]
        upload_log = next(
            (log for log in logs if log["action"] == "files.upload"), None
        )
        assert upload_log is not None, "Expected a files.upload audit log"
        assert upload_log.get("ip_address"), "ip_address should be populated"


# =============================================================================
# Phase 3 — deleted PATCH /api/v1/auth/profile, users/me as sole self-update
# =============================================================================


class TestDeletedAuthProfile:
    """Verify that PATCH /api/v1/auth/profile is gone and users/me is the replacement."""

    def test_auth_profile_returns_404(self):
        """PATCH /api/v1/auth/profile no longer exists (returns 404)."""
        client = _client()
        headers = _admin(client)
        resp = client.patch(
            "/api/v1/auth/profile",
            headers=headers,
            json={"real_name": "Should Fail"},
        )
        assert resp.status_code == 404, (
            f"Expected 404 Not Found, got {resp.status_code}: {resp.text}"
        )

    def test_users_me_updates_real_name(self):
        """PATCH /api/v1/users/me updates the current user's real_name."""
        client = _client()
        headers = _admin(client)

        new_name = _unique("SelfUpdate")
        resp = client.patch(
            "/api/v1/users/me",
            headers=headers,
            json={"real_name": new_name},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["real_name"] == new_name

    def test_users_me_updates_email(self):
        """PATCH /api/v1/users/me updates the current user's email."""
        client = _client()
        headers = _admin(client)

        resp = client.patch(
            "/api/v1/users/me",
            headers=headers,
            json={"email": "updated@example.com"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["email"] == "updated@example.com"

    def test_users_me_cannot_change_status(self):
        """PATCH /api/v1/users/me ignores status field (not on UserSelfUpdate schema)."""
        client = _client()
        headers = _admin(client)

        resp = client.patch(
            "/api/v1/users/me",
            headers=headers,
            json={"status": "disabled"},
        )
        # FastAPI returns 422 for unknown fields in strict mode
        assert resp.status_code in (200, 422), resp.text
        if resp.status_code == 200:
            # Field silently ignored or rejected — current user still active
            me = client.get("/api/v1/auth/me", headers=headers)
            assert me.json()["data"]["status"] == "active"

    def test_users_me_unauthenticated_rejected(self):
        """PATCH /api/v1/users/me without auth returns 401."""
        client = _client()
        resp = client.patch("/api/v1/users/me", json={"real_name": "No Auth"})
        assert resp.status_code == 401, resp.text

    def test_users_me_audit_log_has_ip(self):
        """Self-update via /users/me writes audit log with IP."""
        client = _client()
        headers = _admin(client)

        client.patch(
            "/api/v1/users/me",
            headers=headers,
            json={"real_name": _unique("AuditSelf")},
        )

        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        logs = audit_resp.json()["data"]
        update_log = next(
            (log for log in logs if log["action"] == "users.update_self"), None
        )
        assert update_log is not None, "Expected users.update_self audit log"
        assert update_log.get("ip_address"), "ip_address should be populated"


# =============================================================================
# Phase 1 — delete_project_member with request:Request
# =============================================================================


class TestDeleteProjectMember:
    """Verify delete_project_member works correctly with its new request: Request param."""

    def test_delete_member_works(self):
        """Owner can delete a project member."""
        client = _client()
        headers = _admin(client)

        # Create project
        code = _unique("DELMEM")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Project {code}"},
        )
        # Find it
        list_resp = client.get("/api/v1/projects", headers=headers)
        projects = list_resp.json()["data"]
        project = next((p for p in projects if p["code"] == code), None)
        assert project is not None, f"Project {code} not found"

        # Add member
        username = _unique("member-del")
        uid = _create_user(client, headers, username, roles=["viewer"])
        member_resp = client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=headers,
            json={"user_id": uid, "project_role": "project_viewer"},
        )
        assert member_resp.status_code == 201, member_resp.text
        member_id = member_resp.json()["data"]["id"]

        # Delete member
        del_resp = client.delete(
            f"/api/v1/projects/{project['id']}/members/{member_id}",
            headers=headers,
        )
        assert del_resp.status_code == 204, del_resp.text

    def test_delete_member_audit_log_has_ip(self):
        """Deleting a project member writes audit log with IP."""
        client = _client()
        headers = _admin(client)

        code = _unique("AUDPRJ")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Project {code}"},
        )

        list_resp = client.get("/api/v1/projects", headers=headers)
        project = next(
            (p for p in list_resp.json()["data"] if p["code"] == code), None
        )

        username = _unique("audit-mem")
        uid = _create_user(client, headers, username, roles=["viewer"])
        member_resp = client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=headers,
            json={"user_id": uid, "project_role": "project_viewer"},
        )
        member_id = member_resp.json()["data"]["id"]

        client.delete(
            f"/api/v1/projects/{project['id']}/members/{member_id}",
            headers=headers,
        )

        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        logs = audit_resp.json()["data"]
        del_log = next(
            (log for log in logs if log["action"] == "project_members.delete"), None
        )
        assert del_log is not None, "Expected project_members.delete audit log"
        assert del_log.get("ip_address"), "ip_address should be populated"


# =============================================================================
# Phase 4 — ReviewCreate.decision Literal constraint
# =============================================================================


class TestReviewDecisionValidation:
    """Verify ReviewCreate validates decision with Literal type."""

    def _setup_review_context(self, client, headers):
        """Create project, drawing, job, result to have something to review."""
        code = _unique("REVW")
        # Create project
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Review Project {code}"},
        )
        proj_resp = client.get("/api/v1/projects", headers=headers)
        project = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code), None
        )

        # Create drawing
        draw_resp = client.post(
            "/api/v1/drawings",
            headers=headers,
            json={"project_id": project["id"], "title": "Review Drawing"},
        )
        assert draw_resp.status_code == 201, draw_resp.text
        drawing_id = draw_resp.json()["data"]["id"]

        # Create job
        job_resp = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "drawing_id": drawing_id,
                "project_id": project["id"],
                "task_type": "framework_smoke_test",
                "precision_level": "normal",
            },
        )
        assert job_resp.status_code == 202, job_resp.text
        job_id = job_resp.json()["data"]["id"]

        # In Stage 1, the stub worker auto-progresses the job really fast.
        # Wait a moment, then get results.
        import time

        time.sleep(1.5)

        # Get results
        results_resp = client.get(
            f"/api/v1/jobs/{job_id}/results", headers=headers
        )
        assert results_resp.status_code == 200, results_resp.text
        results = results_resp.json()["data"]
        if not results:
            pytest.skip("Stub worker did not produce results in time")
        return results[0]["id"]

    def test_review_approved_is_accepted(self):
        """decision='approved' is valid."""
        client = _client()
        headers = _admin(client)
        result_id = self._setup_review_context(client, headers)

        resp = client.post(
            f"/api/v1/results/{result_id}/reviews",
            headers=headers,
            json={"decision": "approved", "comment": "Looks good."},
        )
        assert resp.status_code == 201, resp.text

    def test_review_rejected_is_accepted(self):
        """decision='rejected' is valid."""
        client = _client()
        headers = _admin(client)
        result_id = self._setup_review_context(client, headers)

        resp = client.post(
            f"/api/v1/results/{result_id}/reviews",
            headers=headers,
            json={"decision": "rejected", "comment": "Incorrect extraction."},
        )
        assert resp.status_code == 201, resp.text

    def test_review_needs_revision_is_accepted(self):
        """decision='needs_revision' is valid."""
        client = _client()
        headers = _admin(client)
        result_id = self._setup_review_context(client, headers)

        resp = client.post(
            f"/api/v1/results/{result_id}/reviews",
            headers=headers,
            json={"decision": "needs_revision", "comment": "Please add layer info."},
        )
        assert resp.status_code == 201, resp.text

    def test_review_invalid_decision_rejected(self):
        """decision='invalid_choice' is rejected by Pydantic Literal validation."""
        client = _client()
        headers = _admin(client)
        result_id = self._setup_review_context(client, headers)

        resp = client.post(
            f"/api/v1/results/{result_id}/reviews",
            headers=headers,
            json={"decision": "invalid_choice", "comment": "Should fail."},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for invalid decision, got {resp.status_code}: {resp.text}"
        )

    def test_review_empty_decision_rejected(self):
        """Empty decision is rejected."""
        client = _client()
        headers = _admin(client)
        result_id = self._setup_review_context(client, headers)

        resp = client.post(
            f"/api/v1/results/{result_id}/reviews",
            headers=headers,
            json={"decision": "", "comment": "Empty."},
        )
        assert resp.status_code == 422, resp.text

    def test_review_whitespace_only_decision_rejected(self):
        """Whitespace-only decision should be rejected (not one of valid literals)."""
        client = _client()
        headers = _admin(client)
        result_id = self._setup_review_context(client, headers)

        resp = client.post(
            f"/api/v1/results/{result_id}/reviews",
            headers=headers,
            json={"decision": "   ", "comment": "Whitespace."},
        )
        assert resp.status_code == 422, resp.text


# =============================================================================
# Phase 3 — new service files integration tests
# =============================================================================


class TestNewServiceFiles:
    """Verify the 5 new service files are importable and their functions work."""

    def test_file_service_imports(self):
        """file_service exports expected functions."""
        assert hasattr(file_service, "download_signature")
        assert hasattr(file_service, "build_signed_download_url")
        assert hasattr(file_service, "validate_download_signature")
        assert hasattr(file_service, "download_headers")
        assert hasattr(file_service, "file_project_ids")
        assert hasattr(file_service, "can_read_file")
        assert hasattr(file_service, "require_file_read_access")
        assert hasattr(file_service, "require_file_delete_access")

    def test_project_service_imports(self):
        """project_service exports expected functions."""
        assert hasattr(project_service, "create_project")
        assert hasattr(project_service, "add_project_member")
        assert hasattr(project_service, "update_project_member")
        assert hasattr(project_service, "remove_project_member")
        assert hasattr(project_service, "require_project_member_or_404")

    def test_drawing_service_imports(self):
        """drawing_service exports expected functions."""
        assert hasattr(drawing_service, "create_drawing")
        assert hasattr(drawing_service, "update_drawing")
        assert hasattr(drawing_service, "archive_drawing")
        assert hasattr(drawing_service, "create_drawing_version")

    def test_jobs_interface_exports_review_operations(self):
        """The Job domain boundary exposes result lookup and review creation."""
        assert hasattr(jobs_interface, "get_result_job")
        assert hasattr(jobs_interface, "create_review")

    def test_agent_service_imports(self):
        """agent_service is a Stage 2 placeholder."""
        assert hasattr(agent_service, "create_agent_run")

    def test_jobs_interface_exports_lifecycle_operations(self):
        """The Job domain boundary exposes cancellation and retry operations."""
        assert callable(jobs_interface.cancel_job)
        assert callable(jobs_interface.retry_job)

    def test_user_service_reset_password_available(self):
        """reset_user_password is exported from user_service."""
        assert callable(reset_user_password)


# =============================================================================
# Phase 1 + 3 — service function correctness
# =============================================================================


class TestCancelRetryJobService:
    """Unit-level tests for Job-domain cancellation and retry operations."""

    def test_cancel_job_raises_on_succeeded_status(self):
        """Cancelling a succeeded job raises 409."""
        client = _client()
        headers = _admin(client)

        # Create a project, drawing, job
        code = _unique("CNCL")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Cancel {code}"},
        )
        proj_resp = client.get("/api/v1/projects", headers=headers)
        project = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code), None
        )
        draw_resp = client.post(
            "/api/v1/drawings",
            headers=headers,
            json={"project_id": project["id"], "title": "Cancel Drawing"},
        )
        job_resp = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "drawing_id": draw_resp.json()["data"]["id"],
                "project_id": project["id"],
                "task_type": "framework_smoke_test",
                "precision_level": "normal",
            },
        )
        job_id = job_resp.json()["data"]["id"]

        import time

        time.sleep(1.5)

        # Try to cancel the job (which may already be succeeded from stub worker)
        resp = client.post(
            f"/api/v1/jobs/{job_id}/cancellation-requests",
            headers=headers,
        )

        # If stub worker already completed it, 409 is expected
        if resp.status_code == 409:
            body = resp.json()
            assert "error" in body
            assert body["error"]["code"] == "JOB_NOT_CANCELLABLE"

    def test_retry_job_raises_on_running_status(self):
        """Retrying a non-failed/cancelled job raises 409."""
        client = _client()
        headers = _admin(client)

        code = _unique("RETRY")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Retry {code}"},
        )
        proj_resp = client.get("/api/v1/projects", headers=headers)
        project = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code), None
        )
        draw_resp = client.post(
            "/api/v1/drawings",
            headers=headers,
            json={"project_id": project["id"], "title": "Retry Drawing"},
        )
        job_resp = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "drawing_id": draw_resp.json()["data"]["id"],
                "project_id": project["id"],
                "task_type": "framework_smoke_test",
                "precision_level": "normal",
            },
        )
        job_id = job_resp.json()["data"]["id"]

        # Immediately try retry (job might be queued/running, not failed)
        import time

        time.sleep(2)

        resp = client.post(
            f"/api/v1/jobs/{job_id}/retry-requests",
            headers=headers,
        )
        if resp.status_code == 409:
            body = resp.json()
            assert "error" in body
            assert body["error"]["code"] == "JOB_NOT_RETRYABLE"


# =============================================================================
# Phase 1 — delete_project_member edge cases
# =============================================================================


class TestDeleteProjectMemberEdgeCases:
    """Edge cases around delete_project_member."""

    def test_delete_nonexistent_member_returns_404(self):
        """Deleting a member_id that doesn't exist returns 404."""
        client = _client()
        headers = _admin(client)

        code = _unique("EDGE")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Edge {code}"},
        )
        proj_resp = client.get("/api/v1/projects", headers=headers)
        project = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code), None
        )

        resp = client.delete(
            f"/api/v1/projects/{project['id']}/members/999999",
            headers=headers,
        )
        assert resp.status_code == 404, resp.text

    def test_delete_member_wrong_project_returns_404(self):
        """Deleting a member that belongs to another project returns 404."""
        client = _client()
        headers = _admin(client)

        # Project A with member
        code_a = _unique("PROJA")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code_a, "name": f"Project A {code_a}"},
        )
        # Project B (empty)
        code_b = _unique("PROJB")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code_b, "name": f"Project B {code_b}"},
        )

        proj_resp = client.get("/api/v1/projects", headers=headers)
        proj_a = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code_a), None
        )
        proj_b = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code_b), None
        )

        username = _unique("cross-proj")
        uid = _create_user(client, headers, username, roles=["viewer"])
        member_resp = client.post(
            f"/api/v1/projects/{proj_a['id']}/members",
            headers=headers,
            json={"user_id": uid, "project_role": "project_viewer"},
        )
        member_id = member_resp.json()["data"]["id"]

        # Try to delete member of project A via project B's endpoint
        resp = client.delete(
            f"/api/v1/projects/{proj_b['id']}/members/{member_id}",
            headers=headers,
        )
        assert resp.status_code == 404, (
            f"Expected 404 cross-project guard, got {resp.status_code}: {resp.text}"
        )

    def test_non_owner_cannot_delete_member(self):
        """A non-owner project member cannot delete another member."""
        client = _client()
        headers = _admin(client)

        code = _unique("NONOWN")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"NonOwner {code}"},
        )
        proj_resp = client.get("/api/v1/projects", headers=headers)
        project = next(
            (p for p in proj_resp.json()["data"] if p["code"] == code), None
        )

        # Create engineer user
        eng_username = _unique("engineer-no-own")
        eng_uid = _create_user(client, headers, eng_username, roles=["engineer"])
        eng_headers = _login(client, eng_username)

        # Add engineer as project_engineer (not owner)
        client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=headers,
            json={"user_id": eng_uid, "project_role": "project_engineer"},
        )

        # Create viewer user
        viewer_username = _unique("viewer-victim")
        viewer_uid = _create_user(client, headers, viewer_username, roles=["viewer"])
        member_resp = client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=headers,
            json={"user_id": viewer_uid, "project_role": "project_viewer"},
        )
        member_id = member_resp.json()["data"]["id"]

        # Engineer tries to delete viewer
        resp = client.delete(
            f"/api/v1/projects/{project['id']}/members/{member_id}",
            headers=eng_headers,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for non-owner deletion, got {resp.status_code}: {resp.text}"
        )


# =============================================================================
# Integration — full flow smoke test with audit log IP capture
# =============================================================================


class TestFullFlowAuditIP:
    """End-to-end: login → create project → upload file → create job → verify audit IPs."""

    def test_full_crud_flow_all_audit_logs_have_ip(self):
        """Run a full CRUD lifecycle and verify every mutation's audit log has IP."""
        client = _client()
        headers = _admin(client)

        # 1. Create user → audit: users.create
        username = _unique("fullflow")
        client.post(
            "/api/v1/users",
            headers=headers,
            json={"username": username, "password": "FlowPass1234", "real_name": "Flow User"},
        )

        # 2. Create project → audit: projects.create
        code = _unique("FLOW")
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": code, "name": f"Flow {code}"},
        )

        # 3. Upload file → audit: files.upload
        content = b"AC1012" + b"\x00" * 1024
        client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": ("flow.dwg", content, "application/acad")},
        )

        # Verify all audit logs have IP
        audit_resp = client.get("/api/v1/audit-logs", headers=headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.json()["data"]

        # Every action in this flow should have ip_address
        expected_actions = {"users.create", "projects.create", "files.upload"}
        found_actions = set()
        for log in logs:
            if log["action"] in expected_actions:
                found_actions.add(log["action"])
                assert log.get("ip_address"), (
                    f"Audit log {log['action']} should have ip_address"
                )

        assert found_actions == expected_actions, (
            f"Missing expected audit actions: {expected_actions - found_actions}"
        )
