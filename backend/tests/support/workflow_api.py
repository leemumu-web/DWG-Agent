"""Reusable API builders for workflow HTTP integration tests."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.projects.interface import (
    ProjectCreate,
    ProjectMemberCreate,
)
from app.modules.projects.interface import (
    add_project_member as _add_project_member,
)
from app.modules.projects.interface import (
    create_project as _create_project,
)
from tests.support.database import open_test_session


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
        json={"role_code": "operator"},
    )
    assert role_response.status_code == 201, role_response.text
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert login.status_code == 201, login.text
    return user_id, {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def create_project(client: TestClient, owner_headers: dict[str, str]) -> int:
    """Create a project via the service layer (replaces removed HTTP endpoint).

    Keeps the same signature for backward compatibility with existing tests.
    """
    code = f"WFAPI-{uuid4().hex[:6]}"
    me = client.get("/api/v1/auth/me", headers=owner_headers)
    assert me.status_code == 200, me.text
    owner_id = me.json()["data"]["id"]
    with open_test_session() as db:
        project = _create_project(
            db,
            ProjectCreate(code=code, name=f"API Test {code}", description="test"),
            owner_id=owner_id,
        )
        db.commit()
        return project.id


def add_project_member(
    client: TestClient,
    project_id: int,
    user_id: int,
    role: str,
    admin_headers: dict[str, str],
) -> None:
    """Add a project member via the service layer (replaces removed HTTP endpoint).

    Keeps the same signature for backward compatibility with existing tests.
    """
    with open_test_session() as db:
        _add_project_member(
            db,
            project_id,
            ProjectMemberCreate(user_id=user_id, project_role=role),
        )
        db.commit()
