"""Celery entry point for Steel DXF Split 1.5.2."""

from __future__ import annotations

from app.modules.dxf_splitting.execution import run_dxf_splitting
from app.modules.dxf_splitting.pl_execution import run_pl_dxf_splitting
from app.modules.jobs.interface import summarize_job_execution
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_dxf_split.split_steel_dxf", bind=True)
def split_steel_dxf_task(
    self,
    job_id: int,
    attempt: int = 1,
) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_dxf_split"
    run_dxf_splitting(
        job_id,
        worker_name=worker_name,
        expected_attempt=attempt,
    )
    return summarize_job_execution(job_id, "steel_dxf_split")


@celery_app.task(name="app.workers.tasks_pl_dxf_split.split_pl_dxf", bind=True)
def split_pl_dxf_task(
    self,
    job_id: int,
    attempt: int = 1,
) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_pl_dxf_split"
    run_pl_dxf_splitting(
        job_id,
        worker_name=worker_name,
        expected_attempt=attempt,
    )
    return summarize_job_execution(job_id, "pl_dxf_split")
