"""Bounded operations consumed by the maintenance queue.

Celery Beat is intentionally not enabled: every operation is explicitly queued by
an authenticated API call until a durable scheduler/outbox exists.
"""

from app.db.session import SessionLocal
from app.models.control_plane import ControlPlaneEvent
from app.workers.celery_app import celery_app, reconcile_stale_running_jobs


@celery_app.task(name="app.workers.tasks_maintenance.reconcile_stale_jobs")
def reconcile_stale_jobs_task() -> dict[str, int | str]:
    """Recover only attempt-fenced jobs already beyond the stale timeout."""
    recovered = reconcile_stale_running_jobs(session_factory=SessionLocal)
    with SessionLocal() as db:
        db.add(
            ControlPlaneEvent(
                source="worker",
                direction="internal",
                event_type="maintenance.reconcile_stale_jobs.completed",
                severity="warning" if recovered else "info",
                target_kind="maintenance",
                target_id="reconcile_stale_jobs",
                payload_json={"recovered_jobs": recovered},
                message=f"Recovered {recovered} stale running job(s).",
            )
        )
        db.commit()
    return {"operation": "reconcile_stale_jobs", "recovered_jobs": recovered}
