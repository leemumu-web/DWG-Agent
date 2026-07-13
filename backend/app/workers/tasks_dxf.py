from __future__ import annotations

from app.services.cad_batch_service import run_dwg_to_dxf_batch
from app.services.dxf_service import run_dxf_conversion
from app.workers.celery_app import celery_app, summarize_job_execution


@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf", bind=True)
def convert_dwg_to_dxf_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """DWG→DXF 转换 Celery 任务（dxf 队列）。

    任务体 run_dxf_conversion 内部处理状态机与失败标记，不向 Celery 抛异常
    （环境错误除外，由 Celery 重试机制处理）。
    """
    worker_name = self.request.hostname or "celery_dxf"
    run_dxf_conversion(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "dxf_open_source")


@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf_batch", bind=True)
def convert_dwg_to_dxf_batch_task(
    self,
    jobs: list[list[int]],
) -> dict[str, int]:
    """Convert one committed group while retaining one Job per DWG file."""
    pairs = [(int(job_id), int(attempt)) for job_id, attempt in jobs]
    worker_name = self.request.hostname or "celery_dxf_batch"
    return run_dwg_to_dxf_batch(pairs, worker_name=worker_name)
