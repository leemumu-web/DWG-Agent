from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app

_DWG_STUB = b"AC1027" + b"\x00" * 1018  # >= 1024 bytes minimum file size


def auth_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    assert login.status_code == 201, login.text
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_dwg_upload_download_and_audit_flow():
    client = TestClient(app)
    headers = auth_headers(client)

    upload = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("sample.dwg", BytesIO(_DWG_STUB), "application/acad")},
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["data"]["id"]
    assert upload.json()["data"]["file_ext"] == ".dwg"
    assert upload.json()["data"]["sha256"]

    download_url = client.get(f"/api/v1/files/{file_id}/download-url", headers=headers)
    assert download_url.status_code == 200, download_url.text
    assert download_url.json()["data"]["url"].startswith(f"/api/v1/files/{file_id}/download?")
    assert download_url.json()["data"]["expires_in"] == 300

    # Must use signed URL — unsigned download is rejected
    signed_url = download_url.json()["data"]["url"]
    downloaded = client.get(signed_url, headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == _DWG_STUB

    audit_logs = client.get("/api/v1/audit-logs", headers=headers)
    assert audit_logs.status_code == 200, audit_logs.text
    actions = {item["action"] for item in audit_logs.json()["data"]}
    assert "files.upload" in actions
    assert "files.download_url" in actions
    assert "files.download" in actions


def test_non_dwg_upload_is_rejected():
    client = TestClient(app)
    headers = auth_headers(client)
    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("sample.txt", BytesIO(b"bad"), "text/plain")},
    )
    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"


def test_agent_boundary_is_explicitly_disabled_in_stage1():
    client = TestClient(app)
    headers = auth_headers(client)
    response = client.post(
        "/api/v1/agent-runs",
        headers=headers,
        json={"session_id": "sess_test", "task": "placeholder only"},
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "AGENT_DISABLED"
