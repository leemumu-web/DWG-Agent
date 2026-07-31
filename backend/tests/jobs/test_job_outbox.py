from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.modules.identity.interface import User
from app.modules.jobs.interface import JobCreate, create_job
from app.modules.jobs.models import JobDispatch
from app.modules.jobs.outbox import stage_conversion_dispatch, stage_job_dispatch
from app.platform.http.exceptions import AppHTTPException


def _user(db, suffix: str) -> User:
    user = User(
        username=f"outbox-{suffix}",
        real_name=f"Outbox {suffix}",
        password_hash="test-only",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _queued_jobs(db, *, count: int = 2):
    user = _user(db, f"owner-{count}")
    return [
        create_job(
            db,
            JobCreate(
                task_type="convert_dwg_to_dxf",
                params={"file_id": index + 1},
            ),
            created_by=user.id,
        )
        for index in range(count)
    ]


def test_stage_conversion_dispatch_is_atomic_and_unique(db):
    jobs = _queued_jobs(db)

    first = stage_conversion_dispatch(
        db, task_type="convert_dwg_to_dxf", jobs=jobs
    )
    second = stage_conversion_dispatch(
        db, task_type="convert_dwg_to_dxf", jobs=jobs
    )

    assert [row.id for row in second] == [row.id for row in first]
    assert len({row.dispatch_uid for row in first}) == 1
    assert {(row.job_id, row.job_attempt) for row in first} == {
        (job.id, job.attempt) for job in jobs
    }
    assert all(row.dispatch_mode == "conversion_batch" for row in first)
    assert all(row.status == "pending" for row in first)


def test_rollback_removes_job_and_dispatch(db):
    job = _queued_jobs(db, count=1)[0]
    stage_job_dispatch(db, job)
    assert db.scalar(select(func.count()).select_from(JobDispatch)) == 1

    db.rollback()

    assert db.scalar(select(func.count()).select_from(JobDispatch)) == 0


def test_partial_existing_conversion_dispatch_is_rejected(db):
    jobs = _queued_jobs(db)
    stage_conversion_dispatch(
        db, task_type="convert_dwg_to_dxf", jobs=[jobs[0]]
    )

    with pytest.raises(AppHTTPException) as error:
        stage_conversion_dispatch(db, task_type="convert_dwg_to_dxf", jobs=jobs)

    assert error.value.detail["code"] == "JOB_DISPATCH_SET_CONFLICT"


def test_new_job_attempt_gets_a_new_dispatch_intent(db):
    job = _queued_jobs(db, count=1)[0]
    first = stage_job_dispatch(db, job)
    job.attempt = 2
    db.flush()

    second = stage_job_dispatch(db, job)

    assert first.id != second.id
    assert first.job_attempt == 1
    assert second.job_attempt == 2
    assert first.dispatch_uid != second.dispatch_uid


def test_stage_dispatch_rejects_non_queued_job(db):
    job = _queued_jobs(db, count=1)[0]
    job.status = "succeeded"
    db.flush()

    with pytest.raises(AppHTTPException) as error:
        stage_job_dispatch(db, job)

    assert error.value.detail["code"] == "JOB_DISPATCH_STATE_INVALID"
