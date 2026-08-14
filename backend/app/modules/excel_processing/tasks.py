"""Celery entry point for Excel Final processing."""

from __future__ import annotations

from app.modules.excel_processing.execution import run_excel_final_processing
from app.modules.excel_processing.stage2_execution import run_excel_stage2_processing
from app.modules.excel_processing.stage3_execution import run_excel_stage3_processing
from app.modules.jobs.interface import summarize_job_execution
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_excel_final.process_excel_final", bind=True)
def process_excel_final_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """Excel→零件清单 处理 Celery 任务（excel_final 队列）。

    任务体 run_excel_final_processing 内部处理状态机与失败标记，不向 Celery 抛异常。
    """
    worker_name = self.request.hostname or "celery_excel_final"
    run_excel_final_processing(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "excel_final")


@celery_app.task(name="app.workers.tasks_excel_stage2.process_excel_stage2", bind=True)
def process_excel_stage2_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """Run one immutable Excel Stage2 attempt on its dedicated execution plane."""
    worker_name = self.request.hostname or "celery_excel_stage2"
    run_excel_stage2_processing(
        job_id,
        worker_name=worker_name,
        expected_attempt=attempt,
    )
    return summarize_job_execution(job_id, "excel_stage2")


@celery_app.task(name="app.workers.tasks_excel_stage3.process_excel_stage3", bind=True)
def process_excel_stage3_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """Excel 第三阶段处理 — 异孔折判断对接，回填 part 表图形列。"""
    worker_name = self.request.hostname or "celery_excel_stage3"
    run_excel_stage3_processing(
        job_id,
        worker_name=worker_name,
        expected_attempt=attempt,
    )
    return summarize_job_execution(job_id, "excel_stage3")


__all__ = [
    "process_excel_final_task",
    "process_excel_stage2_task",
    "process_excel_stage3_task",
]
