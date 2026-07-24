from __future__ import annotations

import json
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.bootstrap.seed import init_db
from app.main import app
from app.platform.config.settings import settings
from app.platform.database import session as session_module
from app.platform.storage.local import LocalFileStorage

_DWG = b"AC1027" + b"\x00" * 1018


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _viewer_headers(client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "data-console-viewer",
            "password": "ViewerPass1234",
            "real_name": "Data Viewer",
        },
    )
    user_id = created.json()["data"]["id"]
    assigned = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin_headers,
        json={"role_code": "viewer"},
    )
    assert assigned.status_code == 201
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "data-console-viewer", "password": "ViewerPass1234"},
    )
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_data_admin_overview_identifies_environment_without_secrets(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get("/api/v1/data-admin/overview", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["environment"]["app_env"] == settings.app_env
    assert data["environment"]["storage_backend"] == settings.storage_backend
    serialized = json.dumps(data)
    assert "database_url" not in serialized
    assert settings.mysql_password not in serialized or not settings.mysql_password


def test_data_admin_files_are_server_paginated(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)
    for index in range(2):
        uploaded = client.post(
            "/api/v1/files",
            headers={**headers, "Idempotency-Key": f"admin-file-{index}"},
            files={
                "upload": (
                    f"sample-{index}.dwg",
                    BytesIO(_DWG),
                    "application/acad",
                )
            },
        )
        assert uploaded.status_code == 201

    response = client.get(
        "/api/v1/data-admin/files",
        headers=headers,
        params={"page": 1, "page_size": 1, "search": "sample"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["data"]) == 1
    assert payload["pagination"]["total"] == 2
    assert payload["pagination"]["page_size"] == 1
    file_id = payload["data"][0]["id"]
    detail = client.get(f"/api/v1/data-admin/files/{file_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["id"] == file_id
    assert detail.json()["data"]["storage_key"]


def test_data_admin_objects_mark_registration(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    storage.put_fileobj(
        "dwg-original",
        "untracked/object.dwg",
        BytesIO(b"abc"),
        length=3,
        content_type="application/octet-stream",
    )
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get(
        "/api/v1/data-admin/objects",
        headers=headers,
        params={"bucket": "dwg-original", "page_size": 20},
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"][0]
    assert item["storage_key"] == "untracked/object.dwg"
    assert item["registered"] is False
    assert item["file_id"] is None


def test_object_enumeration_releases_database_connection_first(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    storage.put_fileobj(
        "dwg-original",
        "untracked/slow-list.dwg",
        BytesIO(b"abc"),
        length=3,
        content_type="application/octet-stream",
    )
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)
    checkins = 0

    def record_checkin(*_args):
        nonlocal checkins
        checkins += 1

    original_list = storage.list_objects

    def observed_list(*args, **kwargs):
        assert checkins > 0, "DB connection remained checked out during storage enumeration"
        return original_list(*args, **kwargs)

    event.listen(session_module.engine, "checkin", record_checkin)
    monkeypatch.setattr(storage, "list_objects", observed_list)
    try:
        response = client.get(
            "/api/v1/data-admin/objects",
            headers=headers,
            params={"bucket": "dwg-original", "page_size": 20},
        )
    finally:
        event.remove(session_module.engine, "checkin", record_checkin)

    assert response.status_code == 200, response.text


def test_data_admin_rejects_viewer(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    admin = _admin_headers(client)
    viewer = _viewer_headers(client, admin)

    assert client.get("/api/v1/data-admin/overview", headers=viewer).status_code == 403


def test_data_admin_transfers_are_server_paginated(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)
    uploaded = client.post(
        "/api/v1/files",
        headers={**headers, "Idempotency-Key": "transfer-list-source"},
        files={"upload": ("transfer.dwg", BytesIO(_DWG), "application/acad")},
    )
    assert uploaded.status_code == 201

    response = client.get(
        "/api/v1/data-admin/transfers",
        headers=headers,
        params={"direction": "inbound", "page": 1, "page_size": 10},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["status"] == "succeeded"
    assert payload["data"][0]["operation"] == "upload"
    transfer_uid = payload["data"][0]["transfer_uid"]
    detail = client.get(
        f"/api/v1/data-admin/transfers/{transfer_uid}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["transfer_uid"] == transfer_uid
    assert detail.json()["data"]["bucket"] == "dwg-original"
    assert detail.json()["data"]["storage_key"]

    overview = client.get("/api/v1/data-admin/overview", headers=headers)
    assert overview.status_code == 200
    today = overview.json()["data"]["transfers_today"]
    assert today["inbound_succeeded"] == 1
    assert today["outbound_succeeded"] == 0
    assert today["attention_required"] == 0


def test_admin_can_start_and_read_consistency_scan(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    storage.put_fileobj(
        "dwg-original",
        "untracked/scan.dwg",
        BytesIO(b"abc"),
        length=3,
        content_type="application/octet-stream",
    )
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)

    queued = client.post(
        "/api/v1/data-admin/scans",
        headers=headers,
        json={"scope_bucket": "dwg-original"},
    )

    assert queued.status_code == 202, queued.text
    scan_id = queued.json()["data"]["id"]
    detail = client.get(f"/api/v1/data-admin/scans/{scan_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "succeeded"
    assert detail.json()["data"]["untracked_object_count"] == 1
    findings = client.get(
        f"/api/v1/data-admin/scans/{scan_id}/findings",
        headers=headers,
    )
    assert findings.status_code == 200
    assert findings.json()["data"][0]["finding_type"] == "untracked_object"


def test_data_admin_scans_are_server_paginated(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)

    for scope_bucket in ("dwg-original", "dwg-derived"):
        response = client.post(
            "/api/v1/data-admin/scans",
            headers=headers,
            json={"scope_bucket": scope_bucket},
        )
        assert response.status_code == 202, response.text

    history = client.get(
        "/api/v1/data-admin/scans",
        headers=headers,
        params={"page": 1, "page_size": 1},
    )

    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["pagination"]["total"] == 2
    assert len(payload["data"]) == 1
    assert payload["data"][0]["scope_bucket"] == "dwg-derived"


def test_viewer_cannot_read_or_mutate_admin_remediation_data(
    tmp_path,
    monkeypatch,
):
    storage = LocalFileStorage(tmp_path / "storage")
    storage.put_fileobj(
        "dwg-original",
        "untracked/remediation.dwg",
        BytesIO(b"abc"),
        length=3,
        content_type="application/octet-stream",
    )
    storage.put_fileobj(
        "dwg-original",
        "untracked/remediation-keep.dwg",
        BytesIO(b"keep"),
        length=4,
        content_type="application/octet-stream",
    )
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    admin = _admin_headers(client)
    viewer = _viewer_headers(client, admin)
    scan = client.post(
        "/api/v1/data-admin/scans",
        headers=admin,
        json={"scope_bucket": "dwg-original"},
    )
    scan_id = scan.json()["data"]["id"]
    viewer_findings = client.get(
        f"/api/v1/data-admin/scans/{scan_id}/findings",
        headers=viewer,
    )
    assert viewer_findings.status_code == 403
    findings = client.get(
        f"/api/v1/data-admin/scans/{scan_id}/findings",
        headers=admin,
    )
    assert findings.status_code == 200
    finding_id = next(
        item["id"]
        for item in findings.json()["data"]
        if item["storage_key"] == "untracked/remediation.dwg"
    )

    denied_preview = client.post(
        "/api/v1/data-admin/remediations/preview",
        headers=viewer,
        json={"finding_ids": [finding_id], "action": "purge_untracked"},
    )
    assert denied_preview.status_code == 403

    admin_preview = client.post(
        "/api/v1/data-admin/remediations/preview",
        headers=admin,
        json={"finding_ids": [finding_id], "action": "purge_untracked"},
    )
    assert admin_preview.status_code == 200
    preview_data = admin_preview.json()["data"]
    assert preview_data["count"] == 1
    assert preview_data["total_bytes"] == 3
    assert preview_data["confirmation_word"] == "PURGE"
    admin_token = preview_data["token"]
    denied_execute = client.post(
        "/api/v1/data-admin/remediations/execute",
        headers=viewer,
        json={
            "preview_token": admin_token,
            "idempotency_key": "viewer-cannot-purge",
            "confirmation_word": "PURGE",
        },
    )
    assert denied_execute.status_code == 403
    missing_confirmation = client.post(
        "/api/v1/data-admin/remediations/execute",
        headers=admin,
        json={
            "preview_token": admin_token,
            "idempotency_key": "admin-purge-1",
        },
    )
    assert missing_confirmation.status_code == 422
    executed = client.post(
        "/api/v1/data-admin/remediations/execute",
        headers=admin,
        json={
            "preview_token": admin_token,
            "idempotency_key": "admin-purge-1",
            "confirmation_word": "PURGE",
        },
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["data"]["status"] == "succeeded"
    assert not storage.object_exists("dwg-original", "untracked/remediation.dwg")
    resolved = client.get(
        f"/api/v1/data-admin/scans/{scan_id}/findings",
        headers=admin,
        params={"resolution_status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["pagination"]["total"] == 1
    assert resolved.json()["data"][0]["resolution_action"] == "purge_untracked"
