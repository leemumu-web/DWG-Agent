from __future__ import annotations

from app.services.dxf_service import run_dxf_conversion
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf", bind=True)
def convert_dwg_to_dxf_task(self, job_id: int) -> dict[str, int | str]:
    """DWG→DXF 转换 Celery 任务（dxf 队列）。

    任务体 run_dxf_conversion 内部处理状态机与失败标记，不向 Celery 抛异常
    （环境错误除外，由 Celery 重试机制处理）。
    """
    worker_name = self.request.hostname or "celery_dxf"
    run_dxf_conversion(job_id, worker_name=worker_name)
    return {"job_id": job_id, "pipeline": "dxf_open_source"}
