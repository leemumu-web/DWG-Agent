from __future__ import annotations

from app.platform.messaging.celery_app import celery_app, summarize_job_execution
from app.services.dxf_classification_service import run_dxf_classification


@celery_app.task(name="app.workers.tasks_dxf_classification.classify_steel_dxf", bind=True)
def classify_steel_dxf_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_dxf_classification"
    run_dxf_classification(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "steel_dxf_classifier")
