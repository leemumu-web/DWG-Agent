from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.models.job import Job
from app.schemas.job_schema import JobRead
from app.services.job_events import job_event_stream


def _job() -> Job:
    return Job(
        task_type="framework_smoke_test",
        precision_level="normal",
        pipeline="local_stub",
        status="queued",
        progress=0,
        params_json={},
    )


def test_job_read_exposes_durable_progress_payload(db: Session):
    job = _job()
    job.progress_data = {"type": "progress", "message": "stored in MySQL"}
    db.add(job)
    db.commit()

    payload = JobRead.model_validate(job)

    assert payload.progress_data == {"type": "progress", "message": "stored in MySQL"}


def test_job_event_stream_observes_commits_from_another_session(db: Session):
    job = _job()
    db.add(job)
    db.commit()
    job_id = job.id
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    stream = job_event_stream(factory, job_id, poll_interval=0, max_duration=2)
    first = next(stream)
    assert first is not None
    assert first["status"] == "queued"

    with factory() as writer:
        updated = writer.get(Job, job_id)
        assert updated is not None
        updated.status = "running"
        updated.progress = 55
        updated.progress_data = {
            "type": "progress",
            "step_name": "mysql_poll",
            "message": "visible after commit",
        }
        writer.commit()

    assert next(stream) is None
    second = next(stream)
    assert second is not None
    assert second["status"] == "running"
    assert second["progress"] == 55
    assert second["step_name"] == "mysql_poll"
    assert second["message"] == "visible after commit"

    with factory() as writer:
        completed = writer.get(Job, job_id)
        assert completed is not None
        completed.status = "succeeded"
        completed.progress = 100
        completed.progress_data = {"type": "done", "message": "complete"}
        writer.commit()

    assert next(stream) is None
    terminal = next(stream)
    assert terminal is not None
    assert terminal["type"] == "done"
    assert terminal["status"] == "succeeded"


def test_failed_job_emits_error_event(db: Session):
    job = _job()
    job.status = "failed"
    job.error_code = "MYSQL_FAILURE"
    job.error_message = "failed in worker"
    job.progress_data = {"type": "error", "message": "failed in worker"}
    db.add(job)
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    event = next(job_event_stream(factory, job.id, poll_interval=0, max_duration=1))

    assert event is not None
    assert event["type"] == "error"
    assert event["error_code"] == "MYSQL_FAILURE"
    assert event["message"] == "failed in worker"
