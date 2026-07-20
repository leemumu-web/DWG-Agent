from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Update

from app.api.v1.jobs_api import _job_snapshot
from app.main import app
from app.models.audit_log import AuditLog
from app.models.excel_final import ExcelFinalBatch, ExcelFinalPart
from app.models.job import Job, JobStep
from app.platform.database.seed import init_db
from app.platform.http.exceptions import AppHTTPException
from app.schemas.job_schema import JobStepRead
from app.services.job_service import (
    cancel_job,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    dispatch_committed_job,
    fail_job_attempt,
    retry_job,
)


def _job(db: Session, *, status: str, attempt: int = 1, progress: int = 0) -> Job:
    job = Job(
        task_type="framework_smoke_test",
        precision_level="normal",
        pipeline="local_stub",
        status=status,
        attempt=attempt,
        priority=0,
        progress=progress,
        params_json={},
    )
    db.add(job)
    db.commit()
    return job


def test_retry_atomically_creates_a_new_attempt_and_clears_failure(db: Session):
    job = _job(db, status="failed", attempt=3, progress=70)
    job.error_code = "BROKEN"
    job.error_message = "old failure"
    db.commit()

    retried = retry_job(db, job)
    db.commit()

    assert retried.status == "queued"
    assert retried.attempt == 4
    assert retried.progress == 0
    assert retried.error_code is None
    assert retried.error_message is None
    assert retried.started_at is None
    assert retried.finished_at is None
    assert retried.progress_data["attempt"] == 4

    with pytest.raises(AppHTTPException) as duplicate:
        retry_job(db, retried)
    assert duplicate.value.status_code == 409
    assert duplicate.value.detail["code"] == "JOB_NOT_RETRYABLE"


def test_dispatch_includes_the_current_attempt_in_the_celery_message(
    db: Session,
    monkeypatch,
):
    job = _job(db, status="queued", attempt=3)
    captured: list[tuple[int, str, int]] = []

    def capture(job_id: int, pipeline: str, attempt: int) -> str:
        captured.append((job_id, pipeline, attempt))
        return "task-3"

    monkeypatch.setattr("app.services.job_service.enqueue_job", capture)

    assert dispatch_committed_job(db, job) == "task-3"
    assert captured == [(job.id, "local_stub", 3)]


def test_dispatch_compensation_cannot_overwrite_job_claimed_after_read(
    db: Session,
    monkeypatch,
):
    job = _job(db, status="queued", attempt=4)
    other_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def ambiguous_delivery(_job_id: int, _pipeline: str, _attempt: int) -> str:
        raise RuntimeError("broker response lost after delivery")

    monkeypatch.setattr("app.services.job_service.enqueue_job", ambiguous_delivery)
    original_execute = Session.execute
    raced = False

    def claim_before_compensation_update(session, statement, *args, **kwargs):
        nonlocal raced
        if (
            session is db
            and isinstance(statement, Update)
            and statement.table.name == "jobs"
            and not raced
        ):
            raced = True
            with other_factory() as other:
                claimed = claim_queued_job(
                    other,
                    job.id,
                    expected_attempt=4,
                    pipeline="local_stub",
                    progress=10,
                    message="worker claimed delivered task",
                )
                assert claimed is not None
        return original_execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", claim_before_compensation_update)

    task_id = dispatch_committed_job(db, job)

    assert task_id == ""
    db.expire_all()
    current = db.get(Job, job.id, populate_existing=True)
    assert current.status == "running"
    assert current.attempt == 4
    assert current.error_code is None


def test_stale_attempt_cannot_write_progress_or_pending_steps(db: Session):
    job = _job(db, status="running", attempt=2, progress=25)
    db.add(
        JobStep(
            job_id=job.id,
            step_name="stale-attempt-step",
            worker_name="old-worker",
            status="succeeded",
        )
    )

    updated = commit_job_progress(
        db,
        job.id,
        attempt=1,
        progress=90,
        event={"type": "progress", "message": "stale"},
    )

    assert updated is None
    current = db.get(Job, job.id, populate_existing=True)
    assert current is not None
    assert current.progress == 25
    assert db.scalar(select(func.count()).select_from(JobStep)) == 0


def test_progress_update_retries_mysql_1020_then_observes_concurrent_cancel(
    db: Session,
    monkeypatch,
):
    job = _job(db, status="running", attempt=1, progress=30)
    original_execute = db.execute
    injected = False

    def execute_with_concurrent_cancel(statement, *args, **kwargs):
        nonlocal injected
        if isinstance(statement, Update) and statement.table.name == "jobs" and not injected:
            injected = True
            original_execute(
                update(Job).where(Job.id == job.id).values(status="cancelled")
            )
            db.commit()
            raise OperationalError(
                "UPDATE jobs",
                {},
                Exception(1020, "Record has changed since last read"),
            )
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute_with_concurrent_cancel)

    updated = commit_job_progress(
        db,
        job.id,
        attempt=1,
        progress=70,
        event={"type": "progress", "message": "conversion complete"},
    )

    assert injected is True
    assert updated is None
    assert db.get(Job, job.id, populate_existing=True).status == "cancelled"


def test_job_step_schema_exposes_execution_attempt(db: Session):
    job = _job(db, status="running", attempt=3)
    step = JobStep(
        job_id=job.id,
        attempt=3,
        step_name="attempt-aware-step",
        worker_name="worker-3",
        status="succeeded",
    )
    db.add(step)
    db.commit()

    payload = JobStepRead.model_validate(step)

    assert payload.attempt == 3


def test_sse_snapshot_contains_only_current_attempt_steps(db: Session):
    job = _job(db, status="running", attempt=2, progress=40)
    db.add_all(
        [
            JobStep(
                job_id=job.id,
                attempt=1,
                step_name="old-failed-step",
                status="failed",
            ),
            JobStep(
                job_id=job.id,
                attempt=2,
                step_name="current-running-step",
                status="running",
            ),
        ]
    )
    db.commit()

    snapshot = _job_snapshot(db, job.id)

    assert snapshot["attempt"] == 2
    assert snapshot["steps"] == [
        {
            "attempt": 2,
            "step_name": "current-running-step",
            "status": "running",
            "error_message": None,
        }
    ]


def test_stale_attempt_cannot_fail_or_complete_new_worker(db: Session):
    job = _job(db, status="running", attempt=5, progress=40)

    assert (
        fail_job_attempt(
            db,
            job.id,
            attempt=4,
            error_code="OLD_FAILURE",
            error_message="old worker failed",
        )
        is None
    )
    assert (
        complete_job_attempt(
            db,
            job.id,
            attempt=4,
            event={"type": "done", "message": "old worker completed"},
        )
        is None
    )
    current = db.get(Job, job.id, populate_existing=True)
    assert current is not None
    assert current.status == "running"
    assert current.attempt == 5
    assert current.progress == 40


def test_cancellation_is_terminal_for_current_attempt(db: Session):
    job = _job(db, status="running", attempt=2, progress=55)

    cancelled = cancel_job(db, job)
    db.commit()

    assert cancelled.status == "cancelled"
    assert cancelled.attempt == 2
    assert cancelled.progress == 55
    assert cancelled.finished_at is not None
    assert cancelled.progress_data["attempt"] == 2
    assert cancelled.progress_data["type"] == "done"


def test_cancellation_removes_provisional_excel_final_rows(db: Session):
    job = _job(db, status="running", attempt=1, progress=75)
    batch = ExcelFinalBatch(
        job_id=job.id,
        source_type="init_table",
        source_name="partial.xlsx",
        part_count=1,
        component_count=0,
    )
    db.add(batch)
    db.flush()
    db.add(ExcelFinalPart(batch_id=batch.id, seq=1, part_no="PARTIAL"))
    db.commit()

    cancel_job(db, job)
    db.commit()

    assert db.scalar(
        select(func.count())
        .select_from(ExcelFinalBatch)
        .where(ExcelFinalBatch.job_id == job.id)
    ) == 0


def test_cancel_all_locks_exact_active_set_and_reports_broker_purge(
    db: Session,
    monkeypatch,
):
    queued = _job(db, status="queued")
    running = _job(db, status="running", progress=40)
    already_failed = _job(db, status="failed", progress=80)
    db.add(
        ExcelFinalBatch(
            job_id=running.id,
            source_type="init_table",
            source_name="bulk-partial.xlsx",
        )
    )
    db.commit()

    monkeypatch.setattr(
        "app.platform.messaging.celery_app.purge_queued_job_messages",
        lambda: ({"dxf": 2, "excel_final": 3}, {"report": "unavailable"}),
    )
    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    response = client.post("/api/v1/jobs/cancel-all-active", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "cancelled_count": 2,
        "celery_revoked": 5,
        "broker_purged_by_queue": {"dxf": 2, "excel_final": 3},
        "broker_purge_failed_queues": ["report"],
    }
    db.expire_all()
    assert db.get(Job, queued.id).status == "cancelled"
    assert db.get(Job, running.id).status == "cancelled"
    assert db.get(Job, already_failed.id).status == "failed"
    assert db.scalar(
        select(func.count())
        .select_from(ExcelFinalBatch)
        .where(ExcelFinalBatch.job_id == running.id)
    ) == 0

    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "jobs.cancel_all").order_by(AuditLog.id.desc())
    )
    assert audit is not None
    assert audit.after_json["cancelled_ids"] == [queued.id, running.id]
