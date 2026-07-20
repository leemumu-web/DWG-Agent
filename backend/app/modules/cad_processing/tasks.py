"""Celery entry points for the three CAD-processing products."""

from __future__ import annotations

from app.modules.cad_processing.dwg_to_dxf.batch import run_dwg_to_dxf_batch
from app.modules.cad_processing.dwg_to_dxf.execution import run_dxf_conversion
from app.modules.cad_processing.dxf_to_dwg.batch import run_dxf_to_dwg_batch
from app.modules.cad_processing.dxf_to_dwg.execution import run_dxf_to_dwg_conversion
from app.modules.cad_processing.dxf_to_excel.execution import run_dxf2excel_extraction
from app.modules.jobs.interface import summarize_job_execution
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf", bind=True)
def convert_dwg_to_dxf_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_dxf"
    run_dxf_conversion(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "dxf_open_source")


@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf_batch", bind=True)
def convert_dwg_to_dxf_batch_task(self, jobs: list[list[int]]) -> dict[str, int]:
    pairs = [(int(job_id), int(attempt)) for job_id, attempt in jobs]
    worker_name = self.request.hostname or "celery_dxf_batch"
    return run_dwg_to_dxf_batch(pairs, worker_name=worker_name)


@celery_app.task(name="app.workers.tasks_dxf2dwg.convert_dxf_to_dwg", bind=True)
def convert_dxf_to_dwg_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_dxf2dwg"
    run_dxf_to_dwg_conversion(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "dxf2dwg_open_source")


@celery_app.task(name="app.workers.tasks_dxf2dwg.convert_dxf_to_dwg_batch", bind=True)
def convert_dxf_to_dwg_batch_task(self, jobs: list[list[int]]) -> dict[str, int]:
    pairs = [(int(job_id), int(attempt)) for job_id, attempt in jobs]
    worker_name = self.request.hostname or "celery_dxf2dwg_batch"
    return run_dxf_to_dwg_batch(pairs, worker_name=worker_name)


@celery_app.task(name="app.workers.tasks_dxf2excel.extract_dxf_to_excel", bind=True)
def extract_dxf_to_excel_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    worker_name = self.request.hostname or "celery_dxf2excel"
    run_dxf2excel_extraction(job_id, worker_name=worker_name, expected_attempt=attempt)
    return summarize_job_execution(job_id, "dxf2excel")
