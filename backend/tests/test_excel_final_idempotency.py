from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import TASK_EXCEL_FINAL
from app.db.init_db import init_db
from app.main import app
from app.models.file import StoredFile
from app.models.job import Job
from app.models.user import User


def _create_user(db: Session, username: str) -> User:
    user = User(
        username=username,
        real_name=username,
        password_hash="test-only",
        password_algo="argon2id",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _job(*, user_id: int, request_key: str | None) -> Job:
    return Job(
        created_by=user_id,
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        priority=0,
        progress=0,
        params_json={"file_id": 81},
        request_key=request_key,
    )


def test_job_request_key_is_unique_per_actor_and_task(db: Session):
    user = _create_user(db, "idempotency-owner")
    db.add(_job(user_id=user.id, request_key="process:key-1"))
    db.commit()

    db.add(_job(user_id=user.id, request_key="process:key-1"))

    with pytest.raises(IntegrityError):
        db.commit()


def _admin_client(db: Session) -> tuple[TestClient, dict[str, str], User]:
    init_db()
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    return (
        client,
        {"Authorization": f"Bearer {response.json()['data']['access_token']}"},
        admin,
    )


def _excel_file(db: Session, *, owner_id: int, suffix: str) -> StoredFile:
    stored = StoredFile(
        bucket="dwg-reports",
        storage_key=f"tests/idempotency-{suffix}.xlsx",
        original_name=f"parts-{suffix}.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=32,
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        uploaded_by=owner_id,
        status="available",
    )
    db.add(stored)
    db.commit()
    return stored


def test_process_replay_returns_same_job(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, admin = _admin_client(db)
    stored = _excel_file(db, owner_id=admin.id, suffix="replay")
    dispatched: list[int] = []
    monkeypatch.setattr(
        "app.api.v1.excel_final_api.dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
    )
    request_headers = {**headers, "Idempotency-Key": "process-1"}

    first = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}", headers=request_headers
    )
    second = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}", headers=request_headers
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert first.json()["data"]["reused"] is False
    assert second.json()["data"]["reused"] is True
    assert dispatched == [first.json()["data"]["job_id"]]
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_process_rejects_same_key_for_different_file(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, admin = _admin_client(db)
    first_file = _excel_file(db, owner_id=admin.id, suffix="first")
    second_file = _excel_file(db, owner_id=admin.id, suffix="second")
    monkeypatch.setattr(
        "app.api.v1.excel_final_api.dispatch_committed_job",
        lambda _db, _job: None,
    )
    request_headers = {**headers, "Idempotency-Key": "process-conflict"}
    first = client.post(
        f"/api/v1/excel-final/process?file_id={first_file.id}", headers=request_headers
    )

    second = client.post(
        f"/api/v1/excel-final/process?file_id={second_file.id}", headers=request_headers
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
