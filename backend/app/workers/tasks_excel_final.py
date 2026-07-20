from __future__ import annotations

from app.platform.messaging.celery_app import celery_app, summarize_job_execution
from app.services.excel_final_service import run_excel_final_processing


@celery_app.task(name="app.workers.tasks_excel_final.process_excel_final", bind=True)
def process_excel_final_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """Excel→零件清单 处理 Celery 任务（excel_final 队列）。

    任务体 run_excel_final_processing 内部处理状态机与失败标记，不向 Celery 抛异常。
    """
    worker_name = self.request.hostname or "celery_excel_final"
    run_excel_final_processing(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "excel_final")
