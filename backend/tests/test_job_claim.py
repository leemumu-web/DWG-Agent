from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.modules.jobs.interface import Job, claim_queued_job


def _job(db: Session, *, status: str = "queued") -> Job:
    job = Job(
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


def test_only_one_session_can_claim_a_queued_job(db: Session):
    job = _job(db)
    other_session = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    try:
        first = claim_queued_job(
            db,
            job.id,
            pipeline="local_stub",
            progress=20,
            message="claimed",
        )
        second = claim_queued_job(
            other_session,
            job.id,
            pipeline="local_stub",
            progress=20,
            message="duplicate",
        )
    finally:
        other_session.close()

    assert first is not None
    assert first.status == "running"
    assert first.progress == 20
    assert first.started_at is not None
    assert first.progress_data == {
        "type": "status",
        "status": "running",
        "progress": 20,
        "message": "claimed",
        "job_id": job.id,
    }
    assert second is None


def test_claim_rejects_missing_and_non_queued_jobs(db: Session):
    running = _job(db, status="running")
    cancelled = _job(db, status="cancelled")

    assert (
        claim_queued_job(db, running.id, pipeline="local_stub", progress=20, message="duplicate")
        is None
    )
    assert (
        claim_queued_job(db, cancelled.id, pipeline="local_stub", progress=20, message="duplicate")
        is None
    )
    assert (
        claim_queued_job(db, 999_999, pipeline="local_stub", progress=20, message="missing") is None
    )


def test_stale_delivery_cannot_claim_a_newer_attempt(db: Session):
    job = _job(db)
    job.attempt = 2
    db.commit()

    claimed = claim_queued_job(
        db,
        job.id,
        expected_attempt=1,
        pipeline="local_stub",
        progress=10,
        message="stale attempt delivery",
    )

    assert claimed is None
    assert db.get(Job, job.id, populate_existing=True).status == "queued"
