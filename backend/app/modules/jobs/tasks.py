"""Jobs-owned compatibility task names."""

from app.modules.jobs.interface import run_local_stub_job, summarize_job_execution
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_report.run_stub_job", bind=True)
def run_stub_job_task(
    self,
    job_id: int,
    attempt: int = 1,
) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_report"
    run_local_stub_job(
        job_id,
        worker_name=worker_name,
        expected_attempt=attempt,
    )
    return summarize_job_execution(job_id, "local_stub")


__all__ = ["run_stub_job_task"]
