"""Bounded operations consumed by the maintenance queue.

Celery Beat is intentionally not enabled: every operation is explicitly queued by
an authenticated API call until a durable scheduler/outbox exists.
"""

from app.models.control_plane import ControlPlaneEvent
from app.modules.jobs.interface import reconcile_stale_running_jobs
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app
from app.services.daily_archive_service import execute_daily_archive_run


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


@celery_app.task(name="app.workers.tasks_maintenance.create_daily_archive")
def create_daily_archive_task(run_id: int) -> dict[str, int | str]:
    """Create a frozen, non-destructive daily archive and register both outputs."""
    try:
        execute_daily_archive_run(run_id, factory=SessionLocal)
    except Exception:
        with SessionLocal() as db:
            db.add(
                ControlPlaneEvent(
                    source="worker",
                    direction="internal",
                    event_type="maintenance.daily_archive.failed",
                    severity="error",
                    target_kind="daily_archive_run",
                    target_id=str(run_id),
                    message="Daily archive generation failed.",
                )
            )
            db.commit()
        raise
    with SessionLocal() as db:
        db.add(
            ControlPlaneEvent(
                source="worker",
                direction="internal",
                event_type="maintenance.daily_archive.completed",
                severity="info",
                target_kind="daily_archive_run",
                target_id=str(run_id),
                message="Daily archive generation completed.",
            )
        )
        db.commit()
    return {"operation": "create_daily_archive", "archive_run_id": run_id}
