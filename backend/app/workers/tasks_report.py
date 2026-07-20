from __future__ import annotations

import app.modules.files.interface as storage_service
from app.modules.jobs.interface import run_local_stub_job, summarize_job_execution
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app
from app.services.storage_reconciliation_service import execute_scan_run


@celery_app.task(name="app.workers.tasks_report.run_stub_job", bind=True)
def run_stub_job_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_report"
    run_local_stub_job(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "local_stub")


@celery_app.task(name="app.workers.tasks_report.scan_storage_consistency")
def scan_storage_consistency_task(scan_run_id: int) -> dict[str, int | str]:
    execute_scan_run(
        scan_run_id,
        factory=SessionLocal,
        storage=storage_service.get_storage_backend(),
        buckets=settings.minio_bucket_names,
    )
    return {"scan_run_id": scan_run_id, "status": "completed"}
