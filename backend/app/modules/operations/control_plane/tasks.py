"""Explicitly requested control-plane maintenance task; no Beat schedule exists."""

from app.modules.jobs.interface import reconcile_stale_running_jobs
from app.modules.operations.control_plane.interface import record_control_plane_event
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_maintenance.reconcile_stale_jobs")
def reconcile_stale_jobs_task() -> dict[str, int | str]:
    """Recover only attempt-fenced jobs already beyond the stale timeout."""
    recovered = reconcile_stale_running_jobs(session_factory=SessionLocal)
    with SessionLocal() as db:
        record_control_plane_event(
            db,
            source="worker",
            event_type="maintenance.reconcile_stale_jobs.completed",
            severity="warning" if recovered else "info",
            target_kind="maintenance",
            target_id="reconcile_stale_jobs",
            payload={"recovered_jobs": recovered},
            message=f"Recovered {recovered} stale running job(s).",
        )
        db.commit()
    return {"operation": "reconcile_stale_jobs", "recovered_jobs": recovered}


__all__ = ["reconcile_stale_jobs_task"]
