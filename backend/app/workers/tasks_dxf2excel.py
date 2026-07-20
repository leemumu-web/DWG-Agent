from __future__ import annotations

from app.modules.jobs.interface import summarize_job_execution
from app.platform.messaging.celery_app import celery_app
from app.services.dxf2excel_service import run_dxf2excel_extraction


@celery_app.task(name="app.workers.tasks_dxf2excel.extract_dxf_to_excel", bind=True)
def extract_dxf_to_excel_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """DXF→Excel 材料表提取 Celery 任务（dxf2excel 队列）。

    任务体 run_dxf2excel_extraction 内部处理状态机与失败标记，不向 Celery 抛异常
    （环境错误除外，由 Celery 重试机制处理）。
    """
    worker_name = self.request.hostname or "celery_dxf2excel"
    run_dxf2excel_extraction(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "dxf2excel")
