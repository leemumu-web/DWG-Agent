"""Reusable API builders for workflow HTTP integration tests."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app


def client() -> TestClient:
    init_db()
    return TestClient(app, raise_server_exceptions=False)


def admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def create_engineer_user(
    client: TestClient,
    admin_headers: dict[str, str],
    prefix: str = "eng",
) -> tuple[int, dict[str, str]]:
    username = f"{prefix}-{uuid4().hex[:8]}"
    password = "EngPassword1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": f"Eng {prefix}"},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]
    role_response = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin_headers,
        json={"role_code": "engineer"},
    )
    assert role_response.status_code == 201, role_response.text
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert login.status_code == 201, login.text
    return user_id, {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def create_project(client: TestClient, owner_headers: dict[str, str]) -> int:
    code = f"WFAPI-{uuid4().hex[:6]}"
    response = client.post(
        "/api/v1/projects",
        headers=owner_headers,
        json={"code": code, "name": f"API Test {code}", "description": "test"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def add_project_member(
    client: TestClient,
    project_id: int,
    user_id: int,
    role: str,
    admin_headers: dict[str, str],
) -> None:
    response = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=admin_headers,
        json={"user_id": user_id, "project_role": role},
    )
    assert response.status_code == 201, response.text
