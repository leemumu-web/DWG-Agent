from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

import app.db.session as db_session
from app.core.exceptions import AppHTTPException
from app.core.security import create_access_token
from app.db.init_db import init_db
from app.main import app
from app.schemas.project_schema import ProjectCreate
from app.schemas.user_schema import UserCreate
from app.services.user_service import create_user

_DWG_STUB = b"AC1027" + b"\x00" * 1018  # >= 1024 bytes minimum file size


def _client(*, raise_server_exceptions: bool = True) -> TestClient:
    init_db()
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def test_admin_cannot_delete_self():
    client = _client()
    root_headers = _admin_headers(client)
    username = _unique("self-delete-admin")
    password = "AdminPass1234"

    created = client.post(
        "/api/v1/users",
        headers=root_headers,
        json={
            "username": username,
            "password": password,
            "real_name": "Self Delete Guard",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]

    role_resp = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=root_headers,
        json={"role_code": "admin"},
    )
    assert role_resp.status_code == 201, role_resp.text

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert login.status_code == 201, login.text
    token = login.json()["data"]["access_token"]

    response = client.delete(
        f"/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CANNOT_DELETE_SELF"


def test_admin_cannot_disable_self_via_user_patch():
    client = _client()
    headers = _admin_headers(client)
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    user_id = me.json()["data"]["id"]

    response = client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"status": "disabled"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CANNOT_DISABLE_SELF"


def test_admin_cannot_remove_own_role():
    client = _client()
    headers = _admin_headers(client)
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    user_id = me.json()["data"]["id"]
    role_id = next(role["id"] for role in me.json()["data"]["roles"] if role["code"] == "super_admin")

    response = client.delete(
        f"/api/v1/users/{user_id}/roles/{role_id}",
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CANNOT_REMOVE_OWN_ROLE"


def test_create_user_integrity_error_returns_conflict():
    class FailingFlushDb:
        rolled_back = False

        def scalar(self, _statement):
            return None

        def add(self, _user):
            return None

        def flush(self):
            raise IntegrityError("insert sys_users", {}, Exception("duplicate username"))

        def rollback(self):
            self.rolled_back = True

    db = FailingFlushDb()

    with pytest.raises(AppHTTPException) as exc_info:
        create_user(db, UserCreate(username="race", password="Password12345", real_name="Race"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "USERNAME_EXISTS"
    assert db.rolled_back is True


def test_wrong_content_type_returns_validation_error_not_500():
    client = _client(raise_server_exceptions=False)

    response = client.post(
        "/api/v1/auth/sessions",
        data='{"username":"admin","password":"SuperAdminPass1"}',
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_non_access_token_is_rejected_for_authenticated_routes():
    client = _client()
    headers = _admin_headers(client)
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    user_id = me.json()["data"]["id"]
    refresh_like_token = create_access_token(str(user_id), {"type": "refresh"})

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh_like_token}"},
    )

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


def test_login_sets_refresh_cookie_and_refresh_returns_access_token():
    client = _client()

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    assert client.cookies.get("dwg_refresh_token")

    refresh = client.post("/api/v1/auth/tokens/refresh")

    assert refresh.status_code == 200, refresh.text
    refreshed_token = refresh.json()["data"]["access_token"]
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refreshed_token}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["data"]["username"] == "admin"


def test_change_password_requires_current_password_and_updates_credentials():
    client = _client()
    root_headers = _admin_headers(client)
    username = _unique("password-user")
    old_password = "OldPass12345"
    new_password = "NewPass12345"

    created = client.post(
        "/api/v1/users",
        headers=root_headers,
        json={
            "username": username,
            "password": old_password,
            "real_name": "Password User",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=root_headers,
        json={"role_code": "viewer"},
    )

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": old_password},
    )
    assert login.status_code == 201, login.text
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    rejected = client.patch(
        "/api/v1/auth/password",
        headers=headers,
        json={"current_password": "WrongPass1234", "new_password": new_password},
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "INVALID_CURRENT_PASSWORD"

    changed = client.patch(
        "/api/v1/auth/password",
        headers=headers,
        json={"current_password": old_password, "new_password": new_password},
    )
    assert changed.status_code == 200, changed.text

    old_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": old_password},
    )
    assert old_login.status_code == 401, old_login.text

    new_login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": new_password},
    )
    assert new_login.status_code == 201, new_login.text


def test_database_health_ok_with_reachable_db(monkeypatch):
    fresh_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(db_session, "engine", fresh_engine)

    health = db_session.db_health()

    assert health["status"] == "ok"
    assert health["message"] == "Database is reachable."


def test_audit_logs_apply_page_and_page_size():
    client = _client()
    headers = _admin_headers(client)

    response = client.get(
        "/api/v1/audit-logs?page=100&page_size=10",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"] == []
    assert payload["pagination"]["page"] == 100
    assert payload["pagination"]["page_size"] == 10
    assert payload["pagination"]["total"] >= 1


def test_duplicate_project_member_returns_conflict():
    client = _client(raise_server_exceptions=False)
    headers = _admin_headers(client)

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("DUPMEM"), "name": "Duplicate Member Guard"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]
    current_user = client.get("/api/v1/auth/me", headers=headers)
    assert current_user.status_code == 200, current_user.text
    user_id = current_user.json()["data"]["id"]

    response = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=headers,
        json={"user_id": user_id, "project_role": "project_viewer"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "PROJECT_MEMBER_EXISTS"


def test_job_inherits_project_id_from_drawing_id():
    client = _client()
    headers = _admin_headers(client)

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": _unique("JOBDRAW"), "name": "Job Drawing Project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    drawing = client.post(
        "/api/v1/drawings",
        headers=headers,
        json={"project_id": project_id, "drawing_no": _unique("DWG")},
    )
    assert drawing.status_code == 201, drawing.text
    drawing_id = drawing.json()["data"]["id"]

    job = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"drawing_id": drawing_id, "task_type": "framework_smoke_test"},
    )

    assert job.status_code == 202, job.text
    assert job.json()["data"]["project_id"] == project_id


def test_empty_dwg_upload_is_rejected():
    client = _client()
    headers = _admin_headers(client)

    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("empty.dwg", b"", "application/acad")},
    )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "FILE_NOT_DWG"


def test_dwg_upload_rejects_disallowed_mime_type():
    client = _client()
    headers = _admin_headers(client)

    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("mime.dwg", BytesIO(_DWG_STUB), "text/plain")},
    )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "FILE_MIME_NOT_ALLOWED"


def test_dwg_upload_rejects_unknown_ac_version_header():
    client = _client()
    headers = _admin_headers(client)

    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("fake-version.dwg", BytesIO(b"AC0000" + b"\x00" * 1018), "application/acad")},
    )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "FILE_NOT_DWG"


def test_unrelated_user_cannot_access_private_uploaded_file():
    client = _client()
    admin_headers = _admin_headers(client)

    upload = client.post(
        "/api/v1/files",
        headers=admin_headers,
        files={"upload": ("private.dwg", BytesIO(_DWG_STUB), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["data"]["id"]

    viewer_username = _unique("file-viewer")
    viewer_password = "ViewerPass1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": viewer_username,
            "password": viewer_password,
            "real_name": "File Viewer",
        },
    )
    assert created.status_code == 201, created.text
    viewer_id = created.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{viewer_id}/roles",
        headers=admin_headers,
        json={"role_code": "viewer"},
    )

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": viewer_username, "password": viewer_password},
    )
    assert login.status_code == 201, login.text
    viewer_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    listed = client.get("/api/v1/files", headers=viewer_headers)
    assert listed.status_code == 200, listed.text
    assert file_id not in {item["id"] for item in listed.json()["data"]}

    for path in (
        f"/api/v1/files/{file_id}",
        f"/api/v1/files/{file_id}/download-url",
        f"/api/v1/files/{file_id}/download",
    ):
        response = client.get(path, headers=viewer_headers)
        assert response.status_code == 403, response.text


def test_download_url_is_signed_and_rejects_tampering():
    client = _client()
    headers = _admin_headers(client)

    upload = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("signed.dwg", BytesIO(_DWG_STUB), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["data"]["id"]

    download_url = client.get(f"/api/v1/files/{file_id}/download-url", headers=headers)
    assert download_url.status_code == 200, download_url.text
    url = download_url.json()["data"]["url"]
    assert url.startswith(f"/api/v1/files/{file_id}/download?")
    assert "expires=" in url
    assert "signature=" in url

    downloaded = client.get(url, headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == _DWG_STUB

    tampered = client.get(url.replace("signature=", "signature=x"), headers=headers)
    assert tampered.status_code == 403, tampered.text
    assert tampered.json()["error"]["code"] == "INVALID_DOWNLOAD_SIGNATURE"


def test_user_password_requires_minimum_length():
    with pytest.raises(ValidationError):
        UserCreate(username="short-password", password="", real_name="Short Password")


def test_project_name_must_not_be_blank():
    with pytest.raises(ValidationError):
        ProjectCreate(code="EMPTY-NAME", name="")


def test_admin_cannot_grant_super_admin_role():
    client = _client()
    root_headers = _admin_headers(client)

    admin_username = _unique("limited-admin")
    admin_password = "AdminPass1234"
    created_admin = client.post(
        "/api/v1/users",
        headers=root_headers,
        json={
            "username": admin_username,
            "password": admin_password,
            "real_name": "Limited Admin",
        },
    )
    assert created_admin.status_code == 201, created_admin.text
    admin_id = created_admin.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{admin_id}/roles",
        headers=root_headers,
        json={"role_code": "admin"},
    )

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": admin_username, "password": admin_password},
    )
    assert login.status_code == 201, login.text
    admin_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    # Admin cannot grant super_admin role via the role assignment endpoint
    target = client.post(
        "/api/v1/users",
        headers=root_headers,
        json={
            "username": _unique("target-user"),
            "password": "TargetPass1234",
            "real_name": "Target User",
        },
    )
    assert target.status_code == 201, target.text
    target_id = target.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{target_id}/roles",
        headers=root_headers,
        json={"role_code": "viewer"},
    )

    blocked_assign = client.post(
        f"/api/v1/users/{target_id}/roles",
        headers=admin_headers,
        json={"role_code": "super_admin"},
    )
    assert blocked_assign.status_code == 403, blocked_assign.text


def test_project_access_requires_membership_and_role():
    client = _client()
    admin_headers = _admin_headers(client)

    viewer_username = _unique("project-viewer")
    viewer_password = "ViewerPass1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": viewer_username,
            "password": viewer_password,
            "real_name": "Project Viewer",
        },
    )
    assert created.status_code == 201, created.text
    viewer_id = created.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{viewer_id}/roles",
        headers=admin_headers,
        json={"role_code": "viewer"},
    )

    project = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"code": _unique("RBAC"), "name": "RBAC Project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": viewer_username, "password": viewer_password},
    )
    assert login.status_code == 201, login.text
    viewer_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    blocked = client.get(f"/api/v1/projects/{project_id}", headers=viewer_headers)
    assert blocked.status_code == 403, blocked.text

    added = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=admin_headers,
        json={"user_id": viewer_id, "project_role": "project_viewer"},
    )
    assert added.status_code == 201, added.text

    allowed = client.get(f"/api/v1/projects/{project_id}", headers=viewer_headers)
    assert allowed.status_code == 200, allowed.text

    denied_write = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=viewer_headers,
        json={"name": "Viewer Should Not Write"},
    )
    assert denied_write.status_code == 403, denied_write.text


def test_project_scoped_drawing_job_and_result_require_membership():
    client = _client()
    admin_headers = _admin_headers(client)

    viewer_username = _unique("resource-viewer")
    viewer_password = "ViewerPass1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": viewer_username,
            "password": viewer_password,
            "real_name": "Resource Viewer",
        },
    )
    assert created.status_code == 201, created.text
    viewer_id = created.json()["data"]["id"]
    client.post(
        f"/api/v1/users/{viewer_id}/roles",
        headers=admin_headers,
        json={"role_code": "viewer"},
    )

    project = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"code": _unique("RSCOPE"), "name": "Scoped Resource Project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]

    drawing = client.post(
        "/api/v1/drawings",
        headers=admin_headers,
        json={"project_id": project_id, "drawing_no": _unique("RSCOPE-DWG")},
    )
    assert drawing.status_code == 201, drawing.text
    drawing_id = drawing.json()["data"]["id"]

    job = client.post(
        "/api/v1/jobs",
        headers=admin_headers,
        json={"drawing_id": drawing_id, "task_type": "framework_smoke_test"},
    )
    assert job.status_code == 202, job.text
    job_id = job.json()["data"]["id"]

    results = client.get(f"/api/v1/jobs/{job_id}/results", headers=admin_headers)
    assert results.status_code == 200, results.text
    result_id = results.json()["data"][0]["id"]

    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": viewer_username, "password": viewer_password},
    )
    assert login.status_code == 201, login.text
    viewer_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    for path in (
        f"/api/v1/drawings/{drawing_id}",
        f"/api/v1/drawings/{drawing_id}/preview",
        f"/api/v1/jobs/{job_id}",
        f"/api/v1/jobs/{job_id}/results",
        f"/api/v1/results/{result_id}",
        f"/api/v1/results/{result_id}/download-url",
        f"/api/v1/results/{result_id}/reviews",
    ):
        response = client.get(path, headers=viewer_headers)
        assert response.status_code == 403, response.text

    review = client.post(
        f"/api/v1/results/{result_id}/reviews",
        headers=viewer_headers,
        json={"decision": "approved", "comment": "cross-project review should be blocked"},
    )
    assert review.status_code == 403, review.text
