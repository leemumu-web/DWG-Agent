from __future__ import annotations

from app.services.job_service import run_local_stub_job
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_report.run_stub_job", bind=True)
def run_stub_job_task(self, job_id: int) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_report"
    run_local_stub_job(job_id, worker_name=worker_name)
    return {"job_id": job_id, "status": "succeeded"}
