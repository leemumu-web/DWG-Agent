"""Storage consistency scan task; findings are persisted in the files domain."""

import app.modules.files.interface as storage_service
from app.modules.operations.storage_reconciliation.scanning import execute_scan_run
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_report.scan_storage_consistency")
def scan_storage_consistency_task(scan_run_id: int) -> dict[str, int | str]:
    execute_scan_run(
        scan_run_id,
        factory=SessionLocal,
        storage=storage_service.get_storage_backend(),
        buckets=settings.minio_bucket_names,
    )
    return {"scan_run_id": scan_run_id, "status": "completed"}


__all__ = ["scan_storage_consistency_task"]
