from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.modules.identity.interface import User
from app.modules.jobs import outbox
from app.modules.jobs.interface import JobCreate, claim_queued_job, create_job
from app.modules.jobs.models import JobDispatch
from app.modules.jobs.outbox import (
    drain_once,
    lease_next_dispatch,
    retry_delay,
    stage_conversion_dispatch,
    stage_job_dispatch,
)
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now
from tests.support.database import get_test_session_factory


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
    assert all(row.available_at.microsecond == 0 for row in first)


def test_rollback_removes_job_and_dispatch(db):
    job = _queued_jobs(db, count=1)[0]
    stage_job_dispatch(db, job)
    assert db.scalar(select(func.count()).select_from(JobDispatch)) == 1

    db.rollback()

    assert db.scalar(select(func.count()).select_from(JobDispatch)) == 0


def test_eager_runtime_drains_committed_dispatch_through_outbox(db, monkeypatch):
    job = _queued_jobs(db, count=1)[0]
    row = stage_job_dispatch(db, job)
    db.commit()
    monkeypatch.setattr(outbox.settings, "celery_task_always_eager", True)
    monkeypatch.setattr(
        outbox,
        "publish_dispatch",
        lambda lease: lease.dispatch_uid,
    )

    assert outbox.drain_eager_dispatches(db) == 1

    db.refresh(row)
    assert row.status == "delivered"
    assert row.celery_task_id == row.dispatch_uid


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


def test_retry_delay_is_jittered_and_bounded():
    # retry_delay 契约：指数退避 0.5×2^n 封顶 30 秒并加 equal jitter；
    # 边界 0.5-1.0 / 15.0-30.0 验证退避曲线与封顶，防发布失败后
    # 重投节奏失控或租约风暴。
    first = retry_delay(1)
    saturated = retry_delay(100)

    assert 0.5 <= first <= 1.0
    assert 15.0 <= saturated <= 30.0


def test_expired_lease_is_reclaimed(db):
    job = _queued_jobs(db, count=1)[0]
    staged = stage_job_dispatch(db, job)
    db.commit()
    factory = get_test_session_factory()

    first = lease_next_dispatch(factory, lease_seconds=30)
    assert first is not None
    db.execute(
        JobDispatch.__table__.update()
        .where(JobDispatch.dispatch_uid == staged.dispatch_uid)
        .values(lease_expires_at=business_now() - timedelta(seconds=1))
    )
    db.commit()
    second = lease_next_dispatch(factory, lease_seconds=30)

    assert second is not None
    assert second.dispatch_uid == first.dispatch_uid
    assert second.lease_token != first.lease_token


def test_reclaim_only_mutates_the_selected_expired_dispatch_group(db):
    jobs = _queued_jobs(db)
    staged = [stage_job_dispatch(db, job) for job in jobs]
    db.commit()
    factory = get_test_session_factory()

    original_leases = [lease_next_dispatch(factory) for _ in jobs]
    assert all(lease is not None for lease in original_leases)
    expired_at = business_now() - timedelta(seconds=1)
    db.execute(
        JobDispatch.__table__.update()
        .where(JobDispatch.id.in_([row.id for row in staged]))
        .values(lease_expires_at=expired_at)
    )
    db.commit()

    reclaimed = lease_next_dispatch(factory)

    assert reclaimed is not None
    untouched_uid = next(
        lease.dispatch_uid
        for lease in original_leases
        if lease is not None and lease.dispatch_uid != reclaimed.dispatch_uid
    )
    db.expire_all()
    untouched = db.scalar(
        select(JobDispatch).where(JobDispatch.dispatch_uid == untouched_uid)
    )
    assert untouched is not None
    assert untouched.status == "leased"
    assert untouched.lease_expires_at == expired_at


def test_conversion_batch_is_leased_as_one_group(db):
    jobs = _queued_jobs(db)
    staged = stage_conversion_dispatch(
        db, task_type="convert_dwg_to_dxf", jobs=jobs
    )
    db.commit()

    lease = lease_next_dispatch(get_test_session_factory())

    assert lease is not None
    assert lease.dispatch_uid == staged[0].dispatch_uid
    assert lease.mode == "conversion_batch"
    assert lease.jobs == tuple((job.id, job.attempt) for job in jobs)


def test_broker_io_happens_after_the_lease_transaction_commits(db, monkeypatch):
    job = _queued_jobs(db, count=1)[0]
    staged = stage_job_dispatch(db, job)
    db.commit()
    factory = get_test_session_factory()

    def publish_after_commit(lease):
        with factory() as observer:
            persisted = observer.scalar(
                select(JobDispatch).where(
                    JobDispatch.dispatch_uid == lease.dispatch_uid
                )
            )
            assert persisted is not None
            assert persisted.status == "leased"
            assert persisted.lease_token == lease.lease_token
        return lease.dispatch_uid

    monkeypatch.setattr(outbox, "publish_dispatch", publish_after_commit)

    assert drain_once(factory) is True
    db.expire_all()
    delivered = db.get(JobDispatch, staged.id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.celery_task_id == staged.dispatch_uid


def test_publish_response_loss_retries_without_second_job_claim(db, monkeypatch):
    job = _queued_jobs(db, count=1)[0]
    staged = stage_job_dispatch(db, job)
    db.commit()
    factory = get_test_session_factory()
    claims: list[bool] = []

    def publish_then_maybe_raise(lease):
        with factory() as worker_db:
            claimed = claim_queued_job(
                worker_db,
                job.id,
                expected_attempt=job.attempt,
                pipeline=job.pipeline or "local_stub",
                progress=1,
                message="claimed from outbox test",
            )
            claims.append(claimed is not None)
        if len(claims) == 1:
            raise ConnectionError("response lost after broker accepted the task")
        return lease.dispatch_uid

    monkeypatch.setattr(outbox, "publish_dispatch", publish_then_maybe_raise)
    monkeypatch.setattr(outbox, "retry_delay", lambda _attempt: 0.0)

    assert drain_once(factory) is True
    assert drain_once(factory) is True

    db.expire_all()
    current = db.get(JobDispatch, staged.id)
    assert claims == [True, False]
    assert current is not None
    assert current.status == "delivered"
    assert current.delivery_attempts == 1


def test_transient_publish_error_is_sanitized_and_keeps_job_queued(db, monkeypatch):
    job = _queued_jobs(db, count=1)[0]
    staged = stage_job_dispatch(db, job)
    db.commit()

    def fail_with_sensitive_message(_lease):
        raise RuntimeError("mysql://root:secret@database/internal")

    monkeypatch.setattr(outbox, "publish_dispatch", fail_with_sensitive_message)

    assert drain_once(get_test_session_factory()) is True

    db.expire_all()
    pending = db.get(JobDispatch, staged.id)
    current_job = db.get(type(job), job.id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.last_error_code == "JOB_DISPATCH_TEMPORARY_FAILURE"
    assert "secret" not in (pending.last_error_message or "")
    assert current_job is not None
    assert current_job.status == "queued"


def test_invalid_dispatch_mode_fails_queued_attempt_without_publishing(db, monkeypatch):
    job = _queued_jobs(db, count=1)[0]
    staged = stage_job_dispatch(db, job)
    staged.dispatch_mode = "unsupported"
    db.commit()
    factory = get_test_session_factory()
    monkeypatch.setattr(
        "app.modules.jobs.dispatch.enqueue_job",
        lambda *_args, **_kwargs: pytest.fail("invalid dispatch must not publish"),
    )

    assert drain_once(factory) is True

    db.expire_all()
    failed_dispatch = db.get(JobDispatch, staged.id)
    failed_job = db.get(type(job), job.id)
    assert failed_dispatch is not None
    assert failed_dispatch.status == "failed"
    assert failed_dispatch.last_error_code == "JOB_DISPATCH_UNSUPPORTED"
    assert failed_job is not None
    assert failed_job.status == "failed"
    assert failed_job.attempt == 1
