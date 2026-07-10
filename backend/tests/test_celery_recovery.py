from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.models.job import Job
from app.workers.celery_app import (
    cleanup_consumed_broker_messages,
    dispose_inherited_resources,
    reconcile_stale_running_jobs,
)


def _job(db: Session, *, status: str) -> Job:
    job = Job(
        task_type="framework_smoke_test",
        precision_level="normal",
        pipeline="local_stub",
        status=status,
        priority=0,
        progress=20,
        params_json={},
    )
    db.add(job)
    db.commit()
    return job


def test_cleanup_removes_only_consumed_sql_broker_rows(db: Session):
    db.execute(
        text(
            """
            CREATE TABLE test_kombu_message (
                id INTEGER PRIMARY KEY,
                visible BOOLEAN NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
    )
    db.execute(
        text(
            "INSERT INTO test_kombu_message (id, visible, payload) "
            "VALUES (1, 0, 'done'), (2, 0, 'done'), (3, 1, 'queued')"
        )
    )
    db.commit()

    deleted = cleanup_consumed_broker_messages(db.get_bind(), table_name="test_kombu_message")

    remaining = db.execute(text("SELECT id, visible FROM test_kombu_message ORDER BY id")).all()
    assert deleted == 2
    assert remaining == [(3, 1)]


def test_reconcile_marks_only_stale_running_jobs_failed(db: Session):
    stale = _job(db, status="running")
    fresh = _job(db, status="running")
    queued = _job(db, status="queued")
    old = datetime.now(UTC) - timedelta(hours=3)
    db.execute(
        text("UPDATE jobs SET updated_at = :old WHERE id IN (:stale_id, :queued_id)"),
        {"old": old, "stale_id": stale.id, "queued_id": queued.id},
    )
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    recovered = reconcile_stale_running_jobs(factory, timeout_seconds=3600)

    db.expire_all()
    assert recovered == 1
    assert db.get(Job, stale.id).status == "failed"
    assert db.get(Job, stale.id).error_code == "CELERY_WORKER_LOST"
    assert db.get(Job, stale.id).progress_data["type"] == "error"
    assert db.get(Job, fresh.id).status == "running"
    assert db.get(Job, queued.id).status == "queued"


def test_reconcile_does_not_overwrite_job_changed_after_candidate_scan(db: Session, monkeypatch):
    stale = _job(db, status="running")
    old = datetime.now(UTC) - timedelta(hours=3)
    db.execute(
        text("UPDATE jobs SET updated_at = :old WHERE id = :id"), {"old": old, "id": stale.id}
    )
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    original_execute = Session.execute
    calls = 0

    def race_execute(session, statement, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            with factory() as other:
                other.execute(
                    text("UPDATE jobs SET status = 'succeeded' WHERE id = :id"),
                    {"id": stale.id},
                )
                other.commit()
        return original_execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", race_execute)

    recovered = reconcile_stale_running_jobs(factory, timeout_seconds=3600)

    db.expire_all()
    assert recovered == 0
    assert db.get(Job, stale.id).status == "succeeded"


def test_worker_child_disposes_inherited_application_pool():
    calls: list[bool] = []

    class FakeEngine:
        def dispose(self, *, close: bool) -> None:
            calls.append(close)

    dispose_inherited_resources(FakeEngine())

    assert calls == [False]
