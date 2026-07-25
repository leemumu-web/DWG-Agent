"""Post-commit Celery routing and definite-dispatch compensation.

This is the currently implemented direct-dispatch seam. It is not the target
transactional Outbox described by the architecture document.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.jobs.event_stream import make_event
from app.modules.jobs.lifecycle import execute_guarded_job_update
from app.modules.jobs.models import Job
from app.platform.config.constants import (
    JOB_FAILED,
    JOB_QUEUED,
    PIPELINE_DXF,
    PIPELINE_DXF2DWG,
    PIPELINE_DXF2EXCEL,
    PIPELINE_EXCEL_FINAL,
    PIPELINE_STEEL_DXF_CLASSIFIER,
    PIPELINE_STEEL_DXF_SPLIT,
    PIPELINE_STUB,
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
)
from app.platform.database.session import SessionLocal
from app.platform.http.exceptions import AppHTTPException

logger = logging.getLogger(__name__)


def enqueue_stub_job(job_id: int, attempt: int) -> str:
    from app.modules.jobs.tasks import run_stub_job_task

    async_result = run_stub_job_task.delay(job_id, attempt)
    return str(async_result.id)


def enqueue_dxf_job(job_id: int, attempt: int) -> str:
    """投递 DWG→DXF 转换任务到 Celery dxf 队列。"""
    from app.modules.cad_processing.interface import enqueue_dwg_to_dxf_job

    return enqueue_dwg_to_dxf_job(job_id, attempt)


def enqueue_dxf2dwg_job(job_id: int, attempt: int) -> str:
    """投递 DXF→DWG 转换任务到 Celery dxf2dwg 队列。"""
    from app.modules.cad_processing.interface import enqueue_dxf_to_dwg_job

    return enqueue_dxf_to_dwg_job(job_id, attempt)


def enqueue_dxf2excel_job(job_id: int, attempt: int) -> str:
    """投递 DXF→Excel 提取任务到 Celery dxf2excel 队列。"""
    from app.modules.cad_processing.interface import enqueue_dxf_to_excel_job

    return enqueue_dxf_to_excel_job(job_id, attempt)


def enqueue_excel_final_job(job_id: int, attempt: int) -> str:
    """投递 Excel→零件清单 处理任务到 Celery excel_final 队列。"""
    from app.modules.excel_processing.interface import enqueue_excel_final_job as enqueue

    return enqueue(job_id, attempt)


def enqueue_dxf_classification_job(job_id: int, attempt: int) -> str:
    """投递冻结 DXF 分类分流任务。"""
    from app.modules.dxf_classification.interface import enqueue_dxf_classification_job

    return enqueue_dxf_classification_job(job_id, attempt)


def enqueue_dxf_split_job(job_id: int, attempt: int) -> str:
    """投递冻结分类 DXF 的成对拆板任务。"""
    from app.modules.dxf_splitting.interface import enqueue_dxf_splitting_job

    return enqueue_dxf_splitting_job(job_id, attempt)


def enqueue_job(job_id: int, pipeline: str, attempt: int) -> str:
    """按 pipeline 投递到对应 Celery 队列。

    返回 Celery task_id。pipeline 未知时投递到 report 队列（兜底 stub）。
    """
    if pipeline == PIPELINE_DXF:
        return enqueue_dxf_job(job_id, attempt)
    if pipeline == PIPELINE_DXF2DWG:
        return enqueue_dxf2dwg_job(job_id, attempt)
    if pipeline == PIPELINE_DXF2EXCEL:
        return enqueue_dxf2excel_job(job_id, attempt)
    if pipeline == PIPELINE_EXCEL_FINAL:
        return enqueue_excel_final_job(job_id, attempt)
    if pipeline == PIPELINE_STEEL_DXF_CLASSIFIER:
        return enqueue_dxf_classification_job(job_id, attempt)
    if pipeline == PIPELINE_STEEL_DXF_SPLIT:
        return enqueue_dxf_split_job(job_id, attempt)
    return enqueue_stub_job(job_id, attempt)


def dispatch_committed_conversion_batch(
    *,
    task_type: str,
    jobs: list[tuple[int, int]],
) -> str:
    """Send one batch message and compensate definite broker failures.

    Queued attempts are marked failed through guarded updates so an HTTP replay
    can use the normal retry path. Attempts already claimed by a worker win.
    """
    serialized = [[job_id, attempt] for job_id, attempt in jobs]
    try:
        if task_type == TASK_DWG_TO_DXF:
            from app.modules.cad_processing.interface import enqueue_dwg_to_dxf_batch

            return enqueue_dwg_to_dxf_batch(serialized)
        if task_type == TASK_DXF_TO_DWG:
            from app.modules.cad_processing.interface import enqueue_dxf_to_dwg_batch

            return enqueue_dxf_to_dwg_batch(serialized)
        raise ValueError(f"Unsupported conversion batch task type: {task_type}")
    except ValueError:
        raise
    except Exception as exc:
        logger.exception("Celery batch dispatch failed for jobs=%s", [job[0] for job in jobs])
        message = "The conversion batch could not be dispatched to the queue."
        finished_at = datetime.now(UTC)
        with SessionLocal() as compensation_db:
            for job_id, attempt in jobs:
                event = make_event(
                    type_="error",
                    status=JOB_FAILED,
                    progress=0,
                    error_code="JOB_ENQUEUE_FAILED",
                    error_message=message,
                    message=message,
                    job_id=job_id,
                    attempt=attempt,
                )
                compensation_db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status == JOB_QUEUED,
                        Job.attempt == attempt,
                    )
                    .values(
                        status=JOB_FAILED,
                        progress=0,
                        error_code="JOB_ENQUEUE_FAILED",
                        error_message=message,
                        progress_data=event,
                        finished_at=finished_at,
                        updated_at=finished_at,
                    )
                    .execution_options(synchronize_session=False)
                )
            compensation_db.commit()
        raise AppHTTPException(
            503,
            "JOB_ENQUEUE_FAILED",
            "Jobs were saved but the conversion batch could not be dispatched to Celery.",
            {"job_ids": [job_id for job_id, _attempt in jobs]},
        ) from exc


def dispatch_committed_job(db: Session, job: Job) -> str:
    """Dispatch a committed job and compensate a definite broker failure.

    If the broker call raises after delivery, a worker may already have claimed
    the row. In that case its non-queued DB state wins and is never overwritten.
    """
    job_id = job.id
    attempt = job.attempt
    pipeline = job.pipeline or PIPELINE_STUB
    try:
        return enqueue_job(job_id, pipeline, attempt)
    except Exception as exc:
        logger.exception("Celery dispatch failed for job_id=%s", job_id)
        db.rollback()
        message = "The task could not be dispatched to the queue."
        finished_at = datetime.now(UTC)
        event = make_event(
            type_="error",
            status=JOB_FAILED,
            progress=0,
            error_code="JOB_ENQUEUE_FAILED",
            error_message=message,
            message=message,
            job_id=job_id,
            attempt=attempt,
        )
        result = execute_guarded_job_update(
            db,
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JOB_QUEUED,
                Job.attempt == attempt,
            )
            .values(
                status=JOB_FAILED,
                progress=0,
                error_code="JOB_ENQUEUE_FAILED",
                error_message=message,
                progress_data=event,
                finished_at=finished_at,
                updated_at=finished_at,
            )
            .execution_options(synchronize_session=False),
        )
        if result.rowcount != 1:
            db.rollback()
            return ""
        db.commit()
        raise AppHTTPException(
            503,
            "JOB_ENQUEUE_FAILED",
            "Job was saved but could not be dispatched to Celery.",
            {"job_id": job_id},
        ) from exc
