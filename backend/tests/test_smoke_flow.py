from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.main import app


def test_login_project_job_result_flow():
    init_db()
    client = TestClient(app)

    login = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    assert login.status_code == 201, login.text
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_code = f"TEST-{uuid4().hex[:8]}"
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "code": project_code,
            "name": "Smoke Test Project",
            "description": "Created by automated smoke test",
        },
    )
    assert project.status_code == 201, project.text

    job = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "project_id": project.json()["data"]["id"],
            "task_type": "framework_smoke_test",
            "precision_level": "normal",
            "params": {"test": True},
        },
    )
    assert job.status_code == 202, job.text
    job_id = job.json()["data"]["id"]

    detail = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "succeeded"
    assert detail.json()["data"]["progress"] == 100

    results = client.get(f"/api/v1/jobs/{job_id}/results", headers=headers)
    assert results.status_code == 200, results.text
    assert len(results.json()["data"]) == 1
