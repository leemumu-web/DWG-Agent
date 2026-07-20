from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models.audit_log import AuditLog
from app.models.job import Job
from app.models.user import User
from app.platform.database.seed import init_db


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_jobs_can_be_filtered_for_current_file_page(db: Session):
    init_db()
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    db.add_all(
        [
            Job(
                created_by=admin.id,
                task_type="convert_dwg_to_dxf",
                precision_level="normal",
                status="running",
                params_json={"file_id": 101},
            ),
            Job(
                created_by=admin.id,
                task_type="convert_dwg_to_dxf",
                precision_level="normal",
                status="succeeded",
                params_json={"file_id": 202},
            ),
            Job(
                created_by=admin.id,
                task_type="framework_smoke_test",
                precision_level="normal",
                status="failed",
                params_json={"file_id": 303},
            ),
            Job(
                created_by=admin.id,
                task_type="convert_dwg_to_dxf",
                precision_level="normal",
                status="failed",
                params_json={"file_id": 404},
            ),
        ]
    )
    db.commit()
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get(
        "/api/v1/jobs",
        headers=headers,
        params={
            "task_type": "convert_dwg_to_dxf",
            "file_ids": "101,202",
            "page": 1,
            "page_size": 20,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 2
    assert {
        row["params_json"]["file_id"] for row in response.json()["data"]
    } == {101, 202}


def test_jobs_status_and_search_filters_are_server_side(db: Session):
    init_db()
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    db.add_all(
        [
            Job(
                created_by=admin.id,
                task_type="convert_dwg_to_dxf",
                precision_level="normal",
                pipeline="dxf_open_source",
                status="running",
            ),
            Job(
                created_by=admin.id,
                task_type="framework_smoke_test",
                precision_level="normal",
                pipeline="local_stub",
                status="succeeded",
            ),
        ]
    )
    db.commit()
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get(
        "/api/v1/jobs",
        headers=headers,
        params={"status": "active", "search": "dxf_open"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 1
    assert response.json()["data"][0]["pipeline"] == "dxf_open_source"


def test_audit_logs_support_server_side_domain_and_search(db: Session):
    init_db()
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    db.add_all(
        [
            AuditLog(
                actor_user_id=admin.id,
                action="users.create",
                resource_type="user",
                resource_id=7101,
            ),
            AuditLog(
                actor_user_id=admin.id,
                action="jobs.create",
                resource_type="job",
                resource_id=8202,
            ),
        ]
    )
    db.commit()
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get(
        "/api/v1/audit-logs",
        headers=headers,
        params={"action_domain": "users", "search": "7101"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 1
    assert response.json()["data"][0]["resource_id"] == 7101
