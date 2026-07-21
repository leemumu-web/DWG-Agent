"""Daily archive task requested explicitly by an authenticated API call."""

from app.modules.operations.control_plane.interface import record_control_plane_event
from app.modules.operations.daily_archive.execution import execute_daily_archive_run
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_maintenance.create_daily_archive")
def create_daily_archive_task(run_id: int) -> dict[str, int | str]:
    """Create a frozen, non-destructive daily archive and register both outputs."""
    try:
        execute_daily_archive_run(run_id, factory=SessionLocal)
    except Exception:
        with SessionLocal() as db:
            record_control_plane_event(
                db,
                source="worker",
                event_type="maintenance.daily_archive.failed",
                severity="error",
                target_kind="daily_archive_run",
                target_id=str(run_id),
                message="Daily archive generation failed.",
            )
            db.commit()
        raise
    with SessionLocal() as db:
        record_control_plane_event(
            db,
            source="worker",
            event_type="maintenance.daily_archive.completed",
            target_kind="daily_archive_run",
            target_id=str(run_id),
            message="Daily archive generation completed.",
        )
        db.commit()
    return {"operation": "create_daily_archive", "archive_run_id": run_id}


__all__ = ["create_daily_archive_task"]
