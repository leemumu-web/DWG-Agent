from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.excel_processing.models import ExcelFinalBatch
from app.modules.jobs.interface import Job, reconcile_stale_running_jobs, summarize_job_execution
from app.platform.messaging import celery_app as celery_runtime
from app.platform.messaging.celery_app import (
    cleanup_consumed_broker_messages,
    dispose_inherited_resources,
    purge_queued_job_messages,
    update_worker_readiness_marker,
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


def test_cleanup_removes_only_stale_consumed_sql_broker_rows(db: Session):
    db.execute(
        text(
            """
            CREATE TABLE test_kombu_message (
                id INTEGER PRIMARY KEY,
                visible BOOLEAN NOT NULL,
                timestamp DATETIME NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
    )
    fresh_reserved = datetime.now(UTC) - timedelta(minutes=5)
    stale_acked = datetime.now(UTC) - timedelta(hours=24)
    db.execute(
        text(
            "INSERT INTO test_kombu_message (id, visible, timestamp, payload) "
            "VALUES (1, 0, :fresh, 'reserved-not-yet-acked'), "
            "(2, 0, :stale, 'acked-leftover'), "
            "(3, 1, :fresh, 'queued')"
        ),
        {"fresh": fresh_reserved, "stale": stale_acked},
    )
    db.commit()

    deleted = cleanup_consumed_broker_messages(
        db.get_bind(),
        table_name="test_kombu_message",
        now=datetime.now(UTC),
    )

    remaining = db.execute(text("SELECT id, visible FROM test_kombu_message ORDER BY id")).all()
    assert deleted == 1
    # The reserved-but-unacked row (id=1) MUST survive: deleting it would make
    # task_reject_on_worker_lost unable to redeliver after a child loss.
    assert remaining == [(1, 0), (3, 1)]


def test_cleanup_preserves_reserved_message_within_stale_window(db: Session):
    """Regression: a freshly reserved message is never deleted by startup cleanup.

    Before this fix, cleanup deleted every invisible row, which silently
    destroyed messages whose child task was still running. Reproduce the
    reserved-but-unacked state (visible=False, recent timestamp) and assert it
    survives cleanup regardless of how many rows match.
    """
    db.execute(
        text(
            """
            CREATE TABLE test_kombu_message (
                id INTEGER PRIMARY KEY,
                visible BOOLEAN NOT NULL,
                timestamp DATETIME NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
    )
    just_now = datetime.now(UTC) - timedelta(seconds=10)
    db.execute(
        text(
            "INSERT INTO test_kombu_message (id, visible, timestamp, payload) "
            "VALUES (1, 0, :ts, 'reserved-running'), (2, 0, :ts, 'reserved-running-2')"
        ),
        {"ts": just_now},
    )
    db.commit()

    deleted = cleanup_consumed_broker_messages(
        db.get_bind(),
        table_name="test_kombu_message",
        now=datetime.now(UTC),
    )

    remaining = db.execute(text("SELECT id, visible FROM test_kombu_message ORDER BY id")).all()
    assert deleted == 0
    assert remaining == [(1, 0), (2, 0)]


def test_reconcile_marks_only_stale_running_jobs_failed(db: Session):
    stale = _job(db, status="running")
    fresh = _job(db, status="running")
    queued = _job(db, status="queued")
    db.add(
        ExcelFinalBatch(
            job_id=stale.id,
            source_type="init_table",
            source_name="stale-partial.xlsx",
        )
    )
    db.commit()
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
    assert db.scalar(select(ExcelFinalBatch.id).where(ExcelFinalBatch.job_id == stale.id)) is None


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


def test_reconcile_does_not_overwrite_new_attempt_with_stale_timestamp(
    db: Session,
    monkeypatch,
):
    stale = _job(db, status="running")
    old = datetime.now(UTC) - timedelta(hours=3)
    db.execute(
        text("UPDATE jobs SET updated_at = :old WHERE id = :id"),
        {"old": old, "id": stale.id},
    )
    db.commit()
    batch = ExcelFinalBatch(
        job_id=stale.id,
        source_type="init_table",
        source_name="current-attempt.xlsx",
    )
    db.add(batch)
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
                    text("UPDATE jobs SET attempt = attempt + 1 WHERE id = :id"),
                    {"id": stale.id},
                )
                other.commit()
        return original_execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", race_execute)

    recovered = reconcile_stale_running_jobs(factory, timeout_seconds=3600)

    db.expire_all()
    current = db.get(Job, stale.id)
    assert recovered == 0
    assert current.status == "running"
    assert current.attempt == 2
    assert (
        db.scalar(select(ExcelFinalBatch.id).where(ExcelFinalBatch.job_id == stale.id)) == batch.id
    )


def test_worker_child_disposes_inherited_application_pool():
    calls: list[bool] = []

    class FakeEngine:
        def dispose(self, *, close: bool) -> None:
            calls.append(close)

    dispose_inherited_resources(FakeEngine())

    assert calls == [False]


def test_worker_readiness_marker_tracks_ready_and_shutdown(tmp_path):
    marker = tmp_path / "celery-ready"

    update_worker_readiness_marker(True, marker)
    assert marker.read_text(encoding="ascii") == "ready\n"

    update_worker_readiness_marker(False, marker)
    assert not marker.exists()


def test_job_recovery_is_registered_in_generic_worker_ready_seam():
    assert "jobs.reconcile_stale_running" in celery_runtime._worker_ready_callbacks


def test_worker_ready_runs_domain_recovery_before_publishing_ready(monkeypatch):
    order: list[str] = []

    monkeypatch.setattr(celery_runtime, "cleanup_expired_task_results", lambda _app: None)
    monkeypatch.setattr(celery_runtime, "cleanup_consumed_broker_messages", lambda: 0)
    monkeypatch.setattr(
        celery_runtime,
        "run_worker_ready_callbacks",
        lambda: order.append("domain-recovery"),
    )
    monkeypatch.setattr(
        celery_runtime,
        "update_worker_readiness_marker",
        lambda ready: order.append(f"ready:{ready}"),
    )
    monkeypatch.setattr(celery_runtime, "_emit_worker_signal", lambda *args: None)

    class Sender:
        app = celery_runtime.celery_app

    celery_runtime._maintain_mysql_runtime_on_worker_start(Sender())

    assert order == ["domain-recovery", "ready:True"]


def test_task_lifecycle_signals_forward_task_identity_and_correlation_id(monkeypatch):
    recorded: list[tuple[str, str, object | None, str | None]] = []

    def record(
        status: str,
        event_type: str,
        sender=None,
        task_id: str | None = None,
    ) -> None:
        recorded.append((status, event_type, sender, task_id))

    monkeypatch.setattr(celery_runtime, "_emit_worker_signal", record)

    task = object()
    celery_runtime._record_task_start(task_id="task-123", task=task)
    celery_runtime._record_task_finish(task_id="task-123", task=task)

    assert recorded == [
        ("online", "task.started", task, "task-123"),
        ("online", "task.finished", task, "task-123"),
    ]


def test_worker_identity_uses_task_request_hostname_before_unknown_fallback(monkeypatch):
    class Request:
        hostname = "worker-dxf@production-node"

    class Task:
        request = Request()

    monkeypatch.delenv("CELERY_WORKER_NODENAME", raising=False)

    assert celery_runtime._worker_identity(Task()) == "worker-dxf@production-node"


def test_purge_queued_job_messages_uses_transport_channel_and_reports_failures():
    class FakeChannel:
        closed = False

        def queue_purge(self, queue: str) -> int:
            if queue == "broken":
                raise RuntimeError("queue unavailable")
            return {"dxf": 2, "excel_final": 3}.get(queue, 0)

        def close(self) -> None:
            self.closed = True

    class FakeConnection:
        def __init__(self, channel: FakeChannel):
            self._channel = channel

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def channel(self) -> FakeChannel:
            return self._channel

    class FakeApp:
        def __init__(self, connection: FakeConnection):
            self._connection = connection

        def connection_for_write(self) -> FakeConnection:
            return self._connection

    channel = FakeChannel()
    app = FakeApp(FakeConnection(channel))

    purged, errors = purge_queued_job_messages(
        app,
        queue_names=("dxf", "broken", "excel_final"),
    )

    assert purged == {"dxf": 2, "excel_final": 3}
    assert errors == {"broken": "queue unavailable"}
    assert channel.closed is True


def test_task_result_summary_reports_authoritative_job_status_and_attempt(db: Session):
    job = _job(db, status="failed")
    job.attempt = 3
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = summarize_job_execution(job.id, "excel_final", session_factory=factory)

    assert result == {
        "job_id": job.id,
        "pipeline": "excel_final",
        "status": "failed",
        "attempt": 3,
    }
