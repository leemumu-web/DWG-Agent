from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bootstrap.seed import init_db
from app.main import app
from app.models.excel_final import ExcelFinalBatch, ExcelFinalComponent, ExcelFinalPart
from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.platform.config.constants import TASK_EXCEL_FINAL


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _create_user(
    client: TestClient, admin_headers: dict[str, str], prefix: str
) -> tuple[int, dict[str, str]]:
    username = f"{prefix}-{uuid4().hex[:8]}"
    password = "ScopedUserPass1"
    response = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": prefix},
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["data"]["id"]
    role_response = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin_headers,
        json={"role_code": "engineer"},
    )
    assert role_response.status_code == 201, role_response.text
    return user_id, _login(client, username, password)


def _seed_unscoped_job(db: Session, *, owner_id: int, status: str = "queued") -> Job:
    job = Job(
        created_by=owner_id,
        task_type="framework_smoke_test",
        precision_level="normal",
        pipeline="local_stub",
        status=status,
        priority=0,
        progress=0,
        params_json={},
    )
    db.add(job)
    db.commit()
    return job


def test_unscoped_jobs_are_visible_only_to_owner_and_admin(db: Session):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, owner_headers = _create_user(client, admin_headers, "job-owner")
    _, stranger_headers = _create_user(client, admin_headers, "job-stranger")
    job = _seed_unscoped_job(db, owner_id=owner_id)

    owner_ids = {
        item["id"] for item in client.get("/api/v1/jobs", headers=owner_headers).json()["data"]
    }
    stranger_ids = {
        item["id"] for item in client.get("/api/v1/jobs", headers=stranger_headers).json()["data"]
    }
    admin_ids = {
        item["id"] for item in client.get("/api/v1/jobs", headers=admin_headers).json()["data"]
    }

    assert job.id in owner_ids
    assert job.id not in stranger_ids
    assert job.id in admin_ids
    assert client.get(f"/api/v1/jobs/{job.id}", headers=owner_headers).status_code == 200
    assert client.get(f"/api/v1/jobs/{job.id}", headers=admin_headers).status_code == 200


@pytest.mark.parametrize("suffix", ["", "/steps", "/logs", "/results", "/events"])
def test_unscoped_job_read_endpoints_reject_other_users(db: Session, suffix: str):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, _ = _create_user(client, admin_headers, "read-owner")
    _, stranger_headers = _create_user(client, admin_headers, "read-stranger")
    job = _seed_unscoped_job(db, owner_id=owner_id)

    response = client.get(f"/api/v1/jobs/{job.id}{suffix}", headers=stranger_headers)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    ("initial_status", "action"),
    [("queued", "cancellation-requests"), ("failed", "retry-requests")],
)
def test_unscoped_job_write_endpoints_reject_other_users(
    db: Session, initial_status: str, action: str
):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, _ = _create_user(client, admin_headers, "write-owner")
    _, stranger_headers = _create_user(client, admin_headers, "write-stranger")
    job = _seed_unscoped_job(db, owner_id=owner_id, status=initial_status)

    response = client.post(f"/api/v1/jobs/{job.id}/{action}", headers=stranger_headers)

    assert response.status_code == 403, response.text
    db.refresh(job)
    assert job.status == initial_status


def _seed_excel_batch(
    db: Session,
    *,
    owner_id: int,
    task_type: str = TASK_EXCEL_FINAL,
    part_no: str = "P-1",
) -> tuple[StoredFile, Job, ExcelFinalBatch]:
    stored = StoredFile(
        bucket="dwg-reports",
        storage_key=f"tests/{uuid4().hex}.xlsx",
        original_name="parts.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=128,
        sha256=uuid4().hex + uuid4().hex,
        uploaded_by=owner_id,
        status="available",
    )
    db.add(stored)
    db.flush()
    job = Job(
        created_by=owner_id,
        task_type=task_type,
        precision_level="normal",
        pipeline="excel_final",
        status="succeeded",
        priority=0,
        progress=100,
        params_json={"file_id": stored.id},
    )
    db.add(job)
    db.flush()
    batch = ExcelFinalBatch(
        job_id=job.id,
        file_id=stored.id,
        source_type="init_table",
        source_name=stored.original_name,
        component_count=1,
        part_count=1,
        total_net_weight=12.5,
        total_gross_weight=15.0,
    )
    db.add(batch)
    db.flush()
    db.add(ExcelFinalPart(batch_id=batch.id, seq=1, part_no=part_no, material="Q355"))
    db.add(ExcelFinalComponent(batch_id=batch.id, component_no="C-1", component_qty=1))
    db.commit()
    return stored, job, batch


def test_excel_final_queries_are_scoped_by_job_owner(db: Session):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, owner_headers = _create_user(client, admin_headers, "excel-owner")
    _, stranger_headers = _create_user(client, admin_headers, "excel-stranger")
    _, job, batch = _seed_excel_batch(db, owner_id=owner_id)

    owner_batches = client.get("/api/v1/excel-final/batches", headers=owner_headers)
    stranger_batches = client.get("/api/v1/excel-final/batches", headers=stranger_headers)
    stranger_search = client.get("/api/v1/excel-final/parts/search", headers=stranger_headers)

    assert {item["batch_id"] for item in owner_batches.json()["data"]} == {batch.id}
    assert stranger_batches.json()["data"] == []
    assert stranger_search.json()["data"] == []

    protected_urls = [
        f"/api/v1/excel-final/process/{job.id}",
        f"/api/v1/excel-final/process/{job.id}/download",
        f"/api/v1/excel-final/batches/{batch.id}",
        f"/api/v1/excel-final/batches/{batch.id}/parts",
        f"/api/v1/excel-final/batches/{batch.id}/parts/1",
        f"/api/v1/excel-final/batches/{batch.id}/components",
    ]
    for url in protected_urls:
        response = client.get(url, headers=stranger_headers)
        assert response.status_code == 403, f"{url}: {response.text}"


def test_excel_final_overview_is_scoped_and_empty_safe(db: Session):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, owner_headers = _create_user(client, admin_headers, "overview-owner")
    _, stranger_headers = _create_user(client, admin_headers, "overview-stranger")
    _seed_excel_batch(db, owner_id=owner_id)

    owner = client.get("/api/v1/excel-final/overview", headers=owner_headers)
    stranger = client.get("/api/v1/excel-final/overview", headers=stranger_headers)

    assert owner.status_code == 200, owner.text
    assert owner.json()["data"] == {
        "batch_count": 1,
        "part_count": 1,
        "component_count": 1,
        "total_net_weight": 12.5,
        "total_gross_weight": 15.0,
        "latest_created_at": owner.json()["data"]["latest_created_at"],
    }
    assert owner.json()["data"]["latest_created_at"] is not None
    assert stranger.status_code == 200, stranger.text
    assert stranger.json()["data"] == {
        "batch_count": 0,
        "part_count": 0,
        "component_count": 0,
        "total_net_weight": 0.0,
        "total_gross_weight": 0.0,
        "latest_created_at": None,
    }


def test_excel_final_global_queries_ignore_batches_from_other_task_types(db: Session):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    _seed_excel_batch(
        db,
        owner_id=admin.id,
        task_type="framework_smoke_test",
        part_no="ANOMALY-1",
    )

    overview = client.get("/api/v1/excel-final/overview", headers=admin_headers)
    batches = client.get("/api/v1/excel-final/batches", headers=admin_headers)
    search = client.get(
        "/api/v1/excel-final/parts/search?part_no=ANOMALY",
        headers=admin_headers,
    )

    assert overview.status_code == batches.status_code == search.status_code == 200
    assert overview.json()["data"]["batch_count"] == 0
    assert batches.json()["data"] == []
    assert search.json()["data"] == []


def test_excel_final_components_are_server_paginated(db: Session):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, owner_headers = _create_user(client, admin_headers, "component-owner")
    _, _, batch = _seed_excel_batch(db, owner_id=owner_id)
    second = ExcelFinalComponent(
        batch_id=batch.id,
        component_no="C-2",
        component_qty=2,
        total_weight=24.5,
    )
    db.add(second)
    db.commit()

    response = client.get(
        f"/api/v1/excel-final/batches/{batch.id}/components?page=2&page_size=1",
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    assert [item["component_no"] for item in response.json()["data"]] == ["C-2"]
    assert response.json()["pagination"] == {
        "page": 2,
        "page_size": 1,
        "total": 2,
        "total_pages": 2,
    }


def test_excel_final_cannot_process_another_users_upload(db: Session, monkeypatch):
    client = _client()
    admin_headers = _login(client, "admin", "SuperAdminPass1")
    owner_id, _ = _create_user(client, admin_headers, "file-owner")
    _, stranger_headers = _create_user(client, admin_headers, "file-stranger")
    stored, _, _ = _seed_excel_batch(db, owner_id=owner_id)
    monkeypatch.setattr("app.api.v1.excel_final_api.settings.excel_final_pipeline_enabled", True)

    response = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}", headers=stranger_headers
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
