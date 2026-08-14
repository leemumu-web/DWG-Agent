"""Celery message encoding plus transitional direct-dispatch compatibility.

Durable delivery uses ``publish_dispatch`` after an outbox lease commits. The
legacy committed-dispatch functions remain only until all routes are migrated.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
    PIPELINE_EXCEL_STAGE2,
    PIPELINE_EXCEL_STAGE3,
    PIPELINE_REMNANT_CONVERT,
    PIPELINE_REMNANT_PARSE,
    PIPELINE_STEEL_DXF_CLASSIFIER,
    PIPELINE_STEEL_DXF_SPLIT,
    PIPELINE_STUB,
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
    TASK_DXF_TO_EXCEL,
    TASK_EXCEL_FINAL,
    TASK_EXCEL_STAGE2,
    TASK_EXCEL_STAGE3,
    TASK_REMNANT_CONVERT,
    TASK_REMNANT_PARSE,
    TASK_STEEL_DXF_CLASSIFICATION,
    TASK_STEEL_DXF_SPLIT,
)
from app.platform.database.session import SessionLocal
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now

if TYPE_CHECKING:
    from app.modules.jobs.outbox import DispatchLease

logger = logging.getLogger(__name__)

TASK_PIPELINES = {
    TASK_DWG_TO_DXF: PIPELINE_DXF,
    TASK_DXF_TO_DWG: PIPELINE_DXF2DWG,
    TASK_DXF_TO_EXCEL: PIPELINE_DXF2EXCEL,
    TASK_EXCEL_FINAL: PIPELINE_EXCEL_FINAL,
    TASK_EXCEL_STAGE2: PIPELINE_EXCEL_STAGE2,
    TASK_EXCEL_STAGE3: PIPELINE_EXCEL_STAGE3,
    TASK_REMNANT_CONVERT: PIPELINE_REMNANT_CONVERT,
    TASK_REMNANT_PARSE: PIPELINE_REMNANT_PARSE,
    TASK_STEEL_DXF_CLASSIFICATION: PIPELINE_STEEL_DXF_CLASSIFIER,
    TASK_STEEL_DXF_SPLIT: PIPELINE_STEEL_DXF_SPLIT,
}


class PermanentDispatchError(ValueError):
    """A persisted dispatch snapshot cannot be handled by this release."""


def enqueue_stub_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    from app.modules.jobs.tasks import run_stub_job_task

    async_result = run_stub_job_task.apply_async(
        args=[job_id, attempt], task_id=task_id
    )
    return str(async_result.id)


def enqueue_dxf_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递 DWG→DXF 转换任务到 Celery dxf 队列。"""
    from app.modules.cad_processing.interface import enqueue_dwg_to_dxf_job

    return enqueue_dwg_to_dxf_job(job_id, attempt, task_id=task_id)


def enqueue_dxf2dwg_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递 DXF→DWG 转换任务到 Celery dxf2dwg 队列。"""
    from app.modules.cad_processing.interface import enqueue_dxf_to_dwg_job

    return enqueue_dxf_to_dwg_job(job_id, attempt, task_id=task_id)


def enqueue_dxf2excel_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递 DXF→Excel 提取任务到 Celery dxf2excel 队列。"""
    from app.modules.cad_processing.interface import enqueue_dxf_to_excel_job

    return enqueue_dxf_to_excel_job(job_id, attempt, task_id=task_id)


def enqueue_excel_final_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递 Excel→零件清单 处理任务到 Celery excel_final 队列。"""
    from app.modules.excel_processing.interface import enqueue_excel_final_job as enqueue

    return enqueue(job_id, attempt, task_id=task_id)


def enqueue_excel_stage2_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递 BH 左右进与 Excel 第二阶段深化任务。"""
    from app.modules.excel_processing.interface import enqueue_excel_stage2_job as enqueue

    return enqueue(job_id, attempt, task_id=task_id)


def enqueue_excel_stage3_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递 Excel 第三阶段异孔折判断任务。"""
    from app.modules.excel_processing.interface import enqueue_excel_stage3_job as enqueue

    return enqueue(job_id, attempt, task_id=task_id)


def enqueue_dxf_classification_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递冻结 DXF 分类分流任务。"""
    from app.modules.dxf_classification.interface import enqueue_dxf_classification_job

    return enqueue_dxf_classification_job(job_id, attempt, task_id=task_id)


def enqueue_dxf_split_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递冻结分类 DXF 的成对拆板任务。"""
    from app.modules.dxf_splitting.interface import enqueue_dxf_splitting_job

    return enqueue_dxf_splitting_job(job_id, attempt, task_id=task_id)


def enqueue_job(
    job_id: int,
    pipeline: str,
    attempt: int,
    *,
    task_id: str | None = None,
) -> str:
    """按 pipeline 投递到对应 Celery 队列。

    返回 Celery task_id。pipeline 未知时投递到 report 队列（兜底 stub）。
    """
    kwargs = {} if task_id is None else {"task_id": task_id}
    if pipeline == PIPELINE_DXF:
        return enqueue_dxf_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_DXF2DWG:
        return enqueue_dxf2dwg_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_DXF2EXCEL:
        return enqueue_dxf2excel_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_EXCEL_FINAL:
        return enqueue_excel_final_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_EXCEL_STAGE2:
        return enqueue_excel_stage2_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_EXCEL_STAGE3:
        return enqueue_excel_stage3_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_STEEL_DXF_CLASSIFIER:
        return enqueue_dxf_classification_job(job_id, attempt, **kwargs)
    if pipeline == PIPELINE_STEEL_DXF_SPLIT:
        return enqueue_dxf_split_job(job_id, attempt, **kwargs)
    return enqueue_stub_job(job_id, attempt, **kwargs)


def publish_dispatch(lease: DispatchLease) -> str:
    """Publish a leased immutable snapshot with its stable Celery task ID."""
    known_pipelines = {
        PIPELINE_DXF,
        PIPELINE_DXF2DWG,
        PIPELINE_DXF2EXCEL,
        PIPELINE_EXCEL_FINAL,
        PIPELINE_EXCEL_STAGE2,
        PIPELINE_EXCEL_STAGE3,
        PIPELINE_REMNANT_CONVERT,
        PIPELINE_REMNANT_PARSE,
        PIPELINE_STEEL_DXF_CLASSIFIER,
        PIPELINE_STEEL_DXF_SPLIT,
        PIPELINE_STUB,
    }
    if lease.mode == "single":
        expected_pipeline = TASK_PIPELINES.get(lease.task_type, PIPELINE_STUB)
        if (
            len(lease.jobs) != 1
            or lease.pipeline not in known_pipelines
            or lease.pipeline != expected_pipeline
        ):
            raise PermanentDispatchError("invalid single dispatch snapshot")
        job_id, attempt = lease.jobs[0]
        return enqueue_job(
            job_id,
            lease.pipeline,
            attempt,
            task_id=lease.dispatch_uid,
        )
    if lease.mode == "conversion_batch":
        serialized = [[job_id, attempt] for job_id, attempt in lease.jobs]
        if lease.task_type == TASK_DWG_TO_DXF and lease.pipeline == PIPELINE_DXF:
            from app.modules.cad_processing.interface import enqueue_dwg_to_dxf_batch

            return enqueue_dwg_to_dxf_batch(
                serialized,
                task_id=lease.dispatch_uid,
            )
        if lease.task_type == TASK_DXF_TO_DWG and lease.pipeline == PIPELINE_DXF2DWG:
            from app.modules.cad_processing.interface import enqueue_dxf_to_dwg_batch

            return enqueue_dxf_to_dwg_batch(
                serialized,
                task_id=lease.dispatch_uid,
            )
        raise PermanentDispatchError("unsupported conversion batch task type")
    raise PermanentDispatchError("unsupported dispatch mode")


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
        finished_at = business_now()
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
        finished_at = business_now()
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
