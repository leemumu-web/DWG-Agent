from __future__ import annotations


def test_public_lifecycle_fences_progress_and_terminal_state_by_attempt(db) -> None:
    from app.modules.jobs.interface import (
        JobCreate,
        claim_queued_job,
        commit_job_progress,
        complete_job_attempt,
        create_job,
        fail_job_attempt,
        make_event,
    )

    succeeded = create_job(db, JobCreate(task_type="framework_smoke_test"), created_by=None)
    db.commit()
    claimed = claim_queued_job(
        db,
        succeeded.id,
        expected_attempt=1,
        pipeline="local_stub",
        progress=10,
        message="claimed",
    )
    assert claimed is not None

    assert (
        commit_job_progress(
            db,
            succeeded.id,
            attempt=2,
            progress=50,
            event=make_event(type_="progress", message="stale"),
        )
        is None
    )
    progressed = commit_job_progress(
        db,
        succeeded.id,
        attempt=1,
        progress=50,
        event=make_event(type_="progress", message="active"),
    )
    assert progressed is not None
    assert progressed.progress == 50
    completed = complete_job_attempt(
        db,
        succeeded.id,
        attempt=1,
        event=make_event(type_="done", message="complete"),
    )
    assert completed is not None
    assert completed.status == "succeeded"

    failed = create_job(db, JobCreate(task_type="framework_smoke_test"), created_by=None)
    db.commit()
    claimed_failed = claim_queued_job(
        db,
        failed.id,
        expected_attempt=1,
        pipeline="local_stub",
        progress=10,
        message="claimed",
    )
    assert claimed_failed is not None
    terminal = fail_job_attempt(
        db,
        failed.id,
        attempt=1,
        error_code="EXPECTED_FAILURE",
        error_message="expected",
    )
    assert terminal is not None
    assert terminal.status == "failed"


def test_public_lifecycle_cancel_retry_creates_a_new_execution_generation(db) -> None:
    from app.modules.jobs.interface import (
        JobCreate,
        cancel_job,
        claim_queued_job,
        create_job,
        retry_job,
    )

    job = create_job(db, JobCreate(task_type="framework_smoke_test"), created_by=None)
    db.commit()
    cancelled = cancel_job(db, job)
    assert cancelled.status == "cancelled"
    db.commit()

    retried = retry_job(db, cancelled)
    assert retried.status == "queued"
    assert retried.attempt == 2
    db.commit()

    assert (
        claim_queued_job(
            db,
            job.id,
            expected_attempt=1,
            pipeline="local_stub",
            progress=10,
            message="stale delivery",
        )
        is None
    )
    claimed = claim_queued_job(
        db,
        job.id,
        expected_attempt=2,
        pipeline="local_stub",
        progress=10,
        message="current delivery",
    )
    assert claimed is not None
    assert claimed.attempt == 2
