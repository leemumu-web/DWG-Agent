from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _viewer(client: TestClient, admin: dict[str, str]) -> dict[str, str]:
    username = f"native-mysql-viewer-{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/users",
        headers=admin,
        json={
            "username": username,
            "password": "ViewerPass1234",
            "real_name": "Native MySQL Viewer",
        },
    )
    user_id = created.json()["data"]["id"]
    assigned = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin,
        json={"role_code": "viewer"},
    )
    assert assigned.status_code == 201
    return _login(client, username, "ViewerPass1234")


def test_native_mysql_catalog_lists_structure_and_rows_for_authenticated_users():
    init_db()
    client = TestClient(app)
    admin = _login(client, "admin", "SuperAdminPass1")
    viewer = _viewer(client, admin)

    listed = client.get("/api/v1/data-admin/mysql/tables", headers=viewer)
    assert listed.status_code == 200, listed.text
    assert "projects" in [row["name"] for row in listed.json()["data"]]

    described = client.get(
        "/api/v1/data-admin/mysql/tables/projects",
        headers=viewer,
    )
    assert described.status_code == 200, described.text
    columns = {row["name"]: row for row in described.json()["data"]["columns"]}
    assert columns["id"]["primary_key"] is True
    assert columns["name"]["type"]

    rows = client.get(
        "/api/v1/data-admin/mysql/tables/sys_users/rows?page=1&page_size=20",
        headers=viewer,
    )
    assert rows.status_code == 200, rows.text
    assert rows.json()["pagination"]["total"] >= 2
    assert any(row["username"] == "admin" for row in rows.json()["data"])


def test_native_mysql_rows_are_admin_writable_and_reader_is_fail_closed():
    init_db()
    client = TestClient(app)
    admin = _login(client, "admin", "SuperAdminPass1")
    viewer = _viewer(client, admin)

    denied = client.post(
        "/api/v1/data-admin/mysql/tables/projects/rows",
        headers=viewer,
        json={"values": {"code": "DENIED", "name": "Denied", "owner_id": 1}},
    )
    assert denied.status_code == 403

    code = f"NATIVE-{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/data-admin/mysql/tables/projects/rows",
        headers=admin,
        json={
            "values": {
                "code": code,
                "name": "Native console row",
                "owner_id": 1,
                "status": "active",
            }
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["data"]["primary_key"]["id"]

    updated = client.patch(
        "/api/v1/data-admin/mysql/tables/projects/rows",
        headers=admin,
        json={
            "primary_key": {"id": project_id},
            "values": {"name": "Updated from console"},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["name"] == "Updated from console"

    deleted = client.request(
        "DELETE",
        "/api/v1/data-admin/mysql/tables/projects/rows",
        headers=admin,
        json={"primary_key": {"id": project_id}},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["deleted"] is True


def test_native_mysql_catalog_rejects_unknown_tables_and_missing_login():
    init_db()
    client = TestClient(app)
    assert client.get("/api/v1/data-admin/mysql/tables").status_code == 401
    admin = _login(client, "admin", "SuperAdminPass1")
    missing = client.get(
        "/api/v1/data-admin/mysql/tables/not_a_real_table",
        headers=admin,
    )
    assert missing.status_code == 404


def test_native_mysql_write_contract_protects_managed_columns_and_bad_values():
    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    admin = _login(client, "admin", "SuperAdminPass1")

    protected = client.patch(
        "/api/v1/data-admin/mysql/tables/sys_users/rows",
        headers=admin,
        json={
            "primary_key": {"id": 999999},
            "values": {"password_hash": "must-not-be-written"},
        },
    )
    assert protected.status_code == 422, protected.text
    assert protected.json()["error"]["code"] == "PROTECTED_DATABASE_COLUMNS"

    primary_key = client.post(
        "/api/v1/data-admin/mysql/tables/projects/rows",
        headers=admin,
        json={
            "values": {
                "id": 999999,
                "code": "PROTECTED-ID",
                "name": "Protected primary key",
                "status": "active",
            }
        },
    )
    assert primary_key.status_code == 422, primary_key.text
    assert primary_key.json()["error"]["code"] == "PROTECTED_DATABASE_COLUMNS"

    invalid_number = client.post(
        "/api/v1/data-admin/mysql/tables/projects/rows",
        headers=admin,
        json={
            "values": {
                "code": "INVALID-NUMBER",
                "name": "Invalid number",
                "owner_id": "not-a-number",
                "status": "active",
            }
        },
    )
    assert invalid_number.status_code == 422, invalid_number.text
    assert invalid_number.json()["error"]["code"] == "INVALID_DATABASE_VALUE"
