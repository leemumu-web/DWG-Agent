from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.main import app

_DWG_STUB = b"AC1027" + b"\x00" * 1018  # >= 1024 bytes minimum file size


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _viewer_headers(client: TestClient) -> dict[str, str]:
    """Create a fresh viewer-scoped user and return its auth headers."""
    import uuid

    admin_headers = _admin_headers(client)
    username = f"infra-viewer-{uuid.uuid4().hex[:10]}"
    password = "ViewerPass1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": "Infra Viewer"},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]

    assign = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin_headers,
        json={"role_code": "viewer"},
    )
    assert assign.status_code == 201, assign.text

    login = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert login.status_code == 201, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_infrastructure_overview_requires_admin():
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get("/api/v1/system/infrastructure", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["status"] in {"ok", "degraded"}
    assert payload["database"]["status"] == "ok"
    assert payload["database"]["engine"] == "sqlite"  # test isolation uses SQLite
    assert payload["database"]["table_count"] is not None
    assert payload["storage"]["backend"] == "local"
    assert isinstance(payload["storage"]["buckets"], list)
    assert payload["catalog"]["available_files"] == 0
    assert payload["catalog"]["tracked_bytes"] == 0
    assert payload["recovery"]["automated_backup"] is False


def test_infrastructure_overview_reflects_uploaded_file():
    client = TestClient(app)
    headers = _admin_headers(client)

    upload = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("sample.dwg", BytesIO(_DWG_STUB), "application/acad")},
    )
    assert upload.status_code == 201, upload.text

    response = client.get("/api/v1/system/infrastructure", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["catalog"]["available_files"] == 1
    assert payload["catalog"]["tracked_bytes"] == len(_DWG_STUB)
    assert ".dwg" in payload["catalog"]["extensions"]
    # The original bucket should now show at least one tracked file.
    original = next(b for b in payload["storage"]["buckets"] if b["name"] == "dwg-original")
    assert original["tracked_files"] == 1


def test_infrastructure_overview_forbidden_for_non_admin():
    client = TestClient(app)
    viewer = _viewer_headers(client)
    response = client.get("/api/v1/system/infrastructure", headers=viewer)
    assert response.status_code == 403, response.text


def test_infrastructure_overview_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/system/infrastructure")
    assert response.status_code == 401, response.text
