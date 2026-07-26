"""Explicit maintenance task for whole-Workflow physical retention cleanup."""

from app.modules.workflows.retention import execute_retention_purge
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_maintenance.purge_workflow_retention")
def purge_workflow_retention_task(export_uid: str) -> dict[str, int | str]:
    return execute_retention_purge(export_uid, factory=SessionLocal)


__all__ = ["purge_workflow_retention_task"]
