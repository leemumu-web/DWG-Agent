from __future__ import annotations

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


def _viewer_headers(client: TestClient, admin: dict[str, str]) -> dict[str, str]:
    created = client.post(
        "/api/v1/users",
        headers=admin,
        json={
            "username": "mysql-console-viewer",
            "password": "ViewerPass1234",
            "real_name": "MySQL Viewer",
        },
    )
    user_id = created.json()["data"]["id"]
    assert client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin,
        json={"role_code": "viewer"},
    ).status_code == 201
    return _login(client, "mysql-console-viewer", "ViewerPass1234")


def test_mysql_gateway_session_maps_admin_and_sets_scoped_cookie():
    init_db()
    client = TestClient(app)
    admin = _login(client, "admin", "SuperAdminPass1")

    created = client.post("/api/v1/data-admin/mysql-sessions", headers=admin)

    assert created.status_code == 201, created.text
    assert created.json()["data"]["team"] == "dba-admin"
    cookie = created.headers["set-cookie"]
    assert "dwg_dba_token=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/dba/mysql/" in cookie
    assert "SameSite=lax" in cookie
    token = created.cookies["dwg_dba_token"]
    checked = client.get(
        "/api/v1/data-admin/mysql-session",
        headers={"Cookie": f"dwg_dba_token={token}"},
    )
    assert checked.status_code == 200, checked.text
    assert checked.headers["X-User"] == "admin"
    assert checked.headers["X-Team"] == "dba-admin"


def test_mysql_gateway_session_maps_non_admin_to_reader_and_rejects_tampering():
    init_db()
    client = TestClient(app)
    admin = _login(client, "admin", "SuperAdminPass1")
    viewer = _viewer_headers(client, admin)

    created = client.post("/api/v1/data-admin/mysql-sessions", headers=viewer)

    assert created.status_code == 201, created.text
    assert created.json()["data"]["team"] == "dba-reader"
    token = created.cookies["dwg_dba_token"]
    checked = client.get(
        "/api/v1/data-admin/mysql-session",
        headers={"Cookie": f"dwg_dba_token={token}"},
    )
    assert checked.status_code == 200
    assert checked.headers["X-Team"] == "dba-reader"
    denied = client.get(
        "/api/v1/data-admin/mysql-session",
        headers={"Cookie": f"dwg_dba_token={token}x"},
    )
    assert denied.status_code == 401


def test_mysql_gateway_requires_platform_login():
    client = TestClient(app)
    assert client.post("/api/v1/data-admin/mysql-sessions").status_code == 401
    assert client.get("/api/v1/data-admin/mysql-session").status_code == 401
