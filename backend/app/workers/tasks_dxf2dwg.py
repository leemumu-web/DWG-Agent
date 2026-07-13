from __future__ import annotations

from app.services.cad_batch_service import run_dxf_to_dwg_batch
from app.services.dxf2dwg_service import run_dxf_to_dwg_conversion
from app.workers.celery_app import celery_app, summarize_job_execution


@celery_app.task(name="app.workers.tasks_dxf2dwg.convert_dxf_to_dwg", bind=True)
def convert_dxf_to_dwg_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """DXF→DWG 转换 Celery 任务（dxf2dwg 队列）。"""
    worker_name = self.request.hostname or "celery_dxf2dwg"
    run_dxf_to_dwg_conversion(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "dxf2dwg_open_source")


@celery_app.task(name="app.workers.tasks_dxf2dwg.convert_dxf_to_dwg_batch", bind=True)
def convert_dxf_to_dwg_batch_task(
    self,
    jobs: list[list[int]],
) -> dict[str, int]:
    """Convert one committed group while retaining one Job per DXF file."""
    pairs = [(int(job_id), int(attempt)) for job_id, attempt in jobs]
    worker_name = self.request.hostname or "celery_dxf2dwg_batch"
    return run_dxf_to_dwg_batch(pairs, worker_name=worker_name)
