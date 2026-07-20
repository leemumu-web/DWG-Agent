from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.excel_final import ExcelFinalBatch
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.modules.files.interface import StoredFile, save_bytes_as_file
from app.modules.projects.interface import Drawing
from app.platform.config.constants import (
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    JOB_VALIDATING,
    JOB_WAITING_CAD_WORKER,
    PIPELINE_DXF,
    PIPELINE_DXF2DWG,
    PIPELINE_DXF2EXCEL,
    PIPELINE_EXCEL_FINAL,
    PIPELINE_STEEL_DXF_CLASSIFIER,
    PIPELINE_STUB,
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
    TASK_DXF_TO_EXCEL,
    TASK_EXCEL_FINAL,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.http.exceptions import AppHTTPException
from app.schemas.job_schema import JobCreate
from app.services.job_events import make_event, publish_job_event

logger = logging.getLogger(__name__)


def _mysql_error_code(exc: OperationalError) -> int | None:
    args = getattr(exc.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def _execute_guarded_job_update(db: Session, statement):
    """Re-evaluate a conditional job update after MySQL concurrent-change error 1020.

    MySQL can raise 1020 instead of returning zero affected rows when another
    transaction changes the guarded row during an update. After rollback, one
    re-execution is safe only when the status/attempt guard now rejects the row.
    A successful second update is rolled back and the original error is raised
    so callers never commit without their other pending rows.
    """
    try:
        return db.execute(statement)
    except OperationalError as exc:
        if _mysql_error_code(exc) != 1020:
            raise
        db.rollback()
        logger.info("Retrying guarded job update after MySQL concurrent change (1020)")
        result = db.execute(statement)
        if result.rowcount == 0:
            return result
        db.rollback()
        raise exc


def _pipeline_for(task_type: str) -> str:
    """返回 task_type 对应的 pipeline 标识（spec §16.3 管线选择）。"""
    if task_type == TASK_DWG_TO_DXF:
        return PIPELINE_DXF
    if task_type == TASK_DXF_TO_DWG:
        return PIPELINE_DXF2DWG
    if task_type == TASK_DXF_TO_EXCEL:
        return PIPELINE_DXF2EXCEL
    if task_type == TASK_EXCEL_FINAL:
        return PIPELINE_EXCEL_FINAL
    if task_type == TASK_STEEL_DXF_CLASSIFICATION:
        return PIPELINE_STEEL_DXF_CLASSIFIER
    return PIPELINE_STUB


def create_job(
    db: Session,
    payload: JobCreate,
    created_by: int | None,
    *,
    request_key: str | None = None,
) -> Job:
    project_id = payload.project_id
    if project_id is None and payload.drawing_id is not None:
        drawing = db.get(Drawing, payload.drawing_id)
        if drawing:
            project_id = drawing.project_id
    job = Job(
        project_id=project_id,
        drawing_id=payload.drawing_id,
        created_by=created_by,
        task_type=payload.task_type,
        request_key=request_key,
        precision_level=payload.precision_level,
        pipeline=_pipeline_for(payload.task_type),
        status=JOB_QUEUED,
        attempt=1,
        progress=0,
        params_json=payload.params,
    )
    db.add(job)
    db.flush()
    publish_job_event(
        db,
        job.id,
        make_event(type_="status", status=JOB_QUEUED, progress=0, message="任务已入队"),
    )
    return job


def create_conversion_jobs(
    db: Session,
    *,
    task_type: str,
    file_ids: list[int],
    precision_level: str,
    created_by: int,
) -> list[Job]:
    """Validate all sources, then create one ordered Job per unique file ID."""
    expected_ext = ".dwg" if task_type == TASK_DWG_TO_DXF else ".dxf"
    unique_ids = list(dict.fromkeys(file_ids))
    sources: list[StoredFile] = []
    for file_id in unique_ids:
        stored = db.get(StoredFile, file_id)
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(404, "FILE_NOT_FOUND", "File not found.")
        if stored.file_ext.lower() != expected_ext:
            raise AppHTTPException(
                422,
                "INVALID_CONVERSION_SOURCE",
                f"{task_type} requires {expected_ext} source files.",
                {"file_id": file_id, "file_ext": stored.file_ext},
            )
        sources.append(stored)

    jobs: list[Job] = []
    for stored in sources:
        jobs.append(
            create_job(
                db,
                JobCreate(
                    task_type=task_type,
                    precision_level=precision_level,
                    params={"file_id": stored.id, "batch_name": stored.batch_name},
                ),
                created_by=created_by,
            )
        )
    return jobs


def _require_matching_idempotent_job(job: Job, payload: JobCreate) -> None:
    if (
        job.drawing_id != payload.drawing_id
        or job.project_id != payload.project_id
        or job.precision_level != payload.precision_level
        or job.params_json != payload.params
    ):
        raise AppHTTPException(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "The idempotency key was already used with different parameters.",
        )


def create_or_reuse_job(
    db: Session,
    payload: JobCreate,
    *,
    created_by: int,
    request_key: str | None,
) -> tuple[Job, bool]:
    """Create one logical request or return its already committed Job.

    The pre-read handles ordinary HTTP replays. The unique constraint plus
    savepoint handles two processes that race between the pre-read and insert
    without rolling back unrelated work in the caller's outer transaction.
    """
    if request_key is None:
        return create_job(db, payload, created_by), False

    conditions = (
        Job.created_by == created_by,
        Job.task_type == payload.task_type,
        Job.request_key == request_key,
    )
    existing = db.scalar(select(Job).where(*conditions))
    if existing is not None:
        _require_matching_idempotent_job(existing, payload)
        return existing, True

    try:
        with db.begin_nested():
            job = create_job(
                db,
                payload,
                created_by,
                request_key=request_key,
            )
    except IntegrityError:
        # Under MySQL REPEATABLE READ the ordinary pre-read fixes an older
        # consistent snapshot. After the unique-key loser rolls back its
        # savepoint, a locking current read is required to see the winner that
        # committed while the INSERT was waiting on the unique index.
        existing = db.scalar(select(Job).where(*conditions).with_for_update())
        if existing is None:
            raise
        _require_matching_idempotent_job(existing, payload)
        return existing, True
    return job, False


def claim_queued_job(
    db: Session,
    job_id: int,
    *,
    expected_attempt: int | None = None,
    pipeline: str,
    progress: int,
    message: str,
    event_data: dict[str, object] | None = None,
) -> Job | None:
    """Atomically transition one queued job to running.

    The conditional UPDATE is the cross-process idempotency boundary. Only the
    worker whose statement updates one row may perform external side effects.
    """
    started_at = datetime.now(UTC)
    event = make_event(
        type_="status",
        status=JOB_RUNNING,
        progress=progress,
        message=message,
        **(event_data or {}),
    )
    event["job_id"] = job_id
    conditions = [Job.id == job_id, Job.status == JOB_QUEUED]
    if expected_attempt is not None:
        conditions.append(Job.attempt == expected_attempt)
        event["attempt"] = expected_attempt
    result = _execute_guarded_job_update(
        db,
        update(Job)
        .where(*conditions)
        .values(
            status=JOB_RUNNING,
            progress=progress,
            pipeline=pipeline,
            started_at=started_at,
            progress_data=event,
            updated_at=started_at,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def commit_job_progress(
    db: Session,
    job_id: int,
    *,
    attempt: int,
    progress: int,
    event: dict[str, object],
) -> Job | None:
    """Commit pending step data and progress only for the active execution attempt."""
    now = datetime.now(UTC)
    payload = dict(event)
    payload.update(
        {
            "job_id": job_id,
            "status": JOB_RUNNING,
            "progress": progress,
            "attempt": attempt,
        }
    )
    result = _execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JOB_RUNNING,
            Job.attempt == attempt,
        )
        .values(progress=progress, progress_data=payload, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def complete_job_attempt(
    db: Session,
    job_id: int,
    *,
    attempt: int,
    event: dict[str, object],
) -> Job | None:
    """Commit pending result rows and succeed only the worker's own attempt."""
    finished_at = datetime.now(UTC)
    payload = dict(event)
    payload.update(
        {
            "job_id": job_id,
            "type": "done",
            "status": JOB_SUCCEEDED,
            "progress": 100,
            "attempt": attempt,
        }
    )
    result = _execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JOB_RUNNING,
            Job.attempt == attempt,
        )
        .values(
            status=JOB_SUCCEEDED,
            progress=100,
            error_code=None,
            error_message=None,
            progress_data=payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def fail_job_attempt(
    db: Session,
    job_id: int,
    *,
    attempt: int,
    error_code: str,
    error_message: str,
) -> Job | None:
    """Fail only the execution generation that raised the error."""
    finished_at = datetime.now(UTC)
    payload = {
        "job_id": job_id,
        "type": "error",
        "status": JOB_FAILED,
        "attempt": attempt,
        "error_code": error_code,
        "error_message": error_message,
        "message": error_message,
    }
    result = _execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JOB_RUNNING,
            Job.attempt == attempt,
        )
        .values(
            status=JOB_FAILED,
            error_code=error_code,
            error_message=error_message,
            progress_data=payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def enqueue_stub_job(job_id: int, attempt: int) -> str:
    from app.workers.tasks_report import run_stub_job_task

    async_result = run_stub_job_task.delay(job_id, attempt)
    return str(async_result.id)


def enqueue_dxf_job(job_id: int, attempt: int) -> str:
    """投递 DWG→DXF 转换任务到 Celery dxf 队列。"""
    from app.workers.tasks_dxf import convert_dwg_to_dxf_task

    async_result = convert_dwg_to_dxf_task.delay(job_id, attempt)
    return str(async_result.id)


def enqueue_dxf2dwg_job(job_id: int, attempt: int) -> str:
    """投递 DXF→DWG 转换任务到 Celery dxf2dwg 队列。"""
    from app.workers.tasks_dxf2dwg import convert_dxf_to_dwg_task

    async_result = convert_dxf_to_dwg_task.delay(job_id, attempt)
    return str(async_result.id)


def enqueue_dxf2excel_job(job_id: int, attempt: int) -> str:
    """投递 DXF→Excel 提取任务到 Celery dxf2excel 队列。"""
    from app.workers.tasks_dxf2excel import extract_dxf_to_excel_task

    async_result = extract_dxf_to_excel_task.delay(job_id, attempt)
    return str(async_result.id)


def enqueue_excel_final_job(job_id: int, attempt: int) -> str:
    """投递 Excel→零件清单 处理任务到 Celery excel_final 队列。"""
    from app.workers.tasks_excel_final import process_excel_final_task

    async_result = process_excel_final_task.delay(job_id, attempt)
    return str(async_result.id)


def enqueue_dxf_classification_job(job_id: int, attempt: int) -> str:
    """投递冻结 DXF 分类分流任务。"""
    from app.workers.tasks_dxf_classification import classify_steel_dxf_task

    async_result = classify_steel_dxf_task.delay(job_id, attempt)
    return str(async_result.id)


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
            from app.workers.tasks_dxf import convert_dwg_to_dxf_batch_task

            return str(convert_dwg_to_dxf_batch_task.delay(serialized).id)
        if task_type == TASK_DXF_TO_DWG:
            from app.workers.tasks_dxf2dwg import convert_dxf_to_dwg_batch_task

            return str(convert_dxf_to_dwg_batch_task.delay(serialized).id)
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
        result = _execute_guarded_job_update(
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
            .execution_options(synchronize_session=False)
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


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def _mark_job_failed(db: Session, job_id: int, attempt: int, exc: Exception) -> None:
    fail_job_attempt(
        db,
        job_id,
        attempt=attempt,
        error_code="STUB_WORKER_FAILED",
        error_message=_exception_message(exc),
    )


def run_local_stub_job(
    job_id: int,
    worker_name: str = "celery_stub",
    expected_attempt: int = 1,
) -> None:
    """Celery fake task for Stage 1: prove queue/status/result/review plumbing."""
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_STUB,
            progress=20,
            message="任务已接收",
        )
        if job is None:
            return
        attempt = job.attempt
        started_at = job.started_at or datetime.now(UTC)
        db.add(
            JobStep(
                job_id=job.id,
                attempt=attempt,
                step_name="dispatch_stub_worker",
                worker_name=worker_name,
                status="succeeded",
                input_json={"pipeline": PIPELINE_STUB},
                output_json={"message": "Celery framework stub accepted the job."},
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        )
        job = commit_job_progress(
            db,
            job.id,
            attempt=attempt,
            progress=20,
            event=make_event(
                type_="progress",
                status=JOB_RUNNING,
                progress=20,
                message="Celery framework stub accepted the job.",
            ),
        )
        if job is None:
            return

        result_payload = {
            "source": "local_stub",
            "job_id": job.id,
            "task_type": job.task_type,
            "precision_level": job.precision_level,
            "message": "Agent、DWG/DXF 与 CAD Worker 尚未接入；当前结果用于验证任务、结果、下载、审计链路。",
        }
        bucket = settings.minio_bucket_derived
        storage_key = f"jobs/{job.id}/{uuid4().hex}.json"
        raw = json.dumps(result_payload, ensure_ascii=False, indent=2).encode("utf-8")

        result_file = save_bytes_as_file(
            db,
            bucket=bucket,
            storage_key=storage_key,
            original_name=f"job-{job.id}-result.json",
            file_ext=".json",
            content_type="application/json",
            payload=raw,
            uploaded_by=job.created_by,
        )

        result = AnalysisResult(
            job_id=job.id,
            drawing_id=job.drawing_id,
            result_type=job.task_type,
            result_json=result_payload,
            confidence=Decimal("1.0000"),
            result_file_id=result_file.id,
            algorithm_version="framework-stub-v0.1",
            tool_version="local-stub",
            status="succeeded",
        )
        db.add(result)
        db.add(
            JobStep(
                job_id=job.id,
                attempt=attempt,
                step_name="write_stub_result",
                worker_name=worker_name,
                status="succeeded",
                input_json={"result_file_id": result_file.id},
                output_json={"analysis_result": "created"},
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        complete_job_attempt(
            db,
            job.id,
            attempt=attempt,
            event=make_event(
                type_="done", status=JOB_SUCCEEDED, progress=100, message="任务已完成"
            ),
        )
    except Exception as exc:
        db.rollback()
        if "attempt" in locals():
            _mark_job_failed(db, job_id, attempt, exc)
        raise
    finally:
        db.close()


def cancel_job(db: Session, job: Job) -> Job:
    """Atomically cancel one active job without overwriting a worker terminal state."""
    cancellable_statuses = (
        JOB_PENDING,
        JOB_QUEUED,
        JOB_RUNNING,
        JOB_VALIDATING,
        JOB_WAITING_CAD_WORKER,
    )
    if job.status not in cancellable_statuses:
        raise AppHTTPException(
            409,
            "JOB_NOT_CANCELLABLE",
            f"Job cannot be cancelled because it is already {job.status}.",
        )
    finished_at = datetime.now(UTC)
    payload = make_event(
        type_="done",
        status=JOB_CANCELLED,
        progress=job.progress,
        message="任务已取消",
        attempt=job.attempt,
    )
    payload["job_id"] = job.id
    result = _execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job.id,
            Job.status.in_(cancellable_statuses),
            Job.attempt == job.attempt,
        )
        .values(
            status=JOB_CANCELLED,
            progress_data=payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.get(Job, job.id, populate_existing=True)
        current_status = current.status if current is not None else "missing"
        raise AppHTTPException(
            409,
            "JOB_NOT_CANCELLABLE",
            f"Job cannot be cancelled because it is already {current_status}.",
        )
    db.execute(delete(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job.id))
    db.expire(job)
    return db.get(Job, job.id, populate_existing=True) or job


def retry_job(db: Session, job: Job) -> Job:
    """Atomically enqueue a new generation for a failed or cancelled job."""
    if job.status not in (JOB_FAILED, JOB_CANCELLED):
        raise AppHTTPException(
            409,
            "JOB_NOT_RETRYABLE",
            f"Job cannot be retried because it is {job.status}. Only failed or cancelled jobs can be retried.",
        )
    previous_attempt = job.attempt
    next_attempt = previous_attempt + 1
    now = datetime.now(UTC)
    payload = make_event(
        type_="status",
        status=JOB_QUEUED,
        progress=0,
        message="任务已重新入队",
        attempt=next_attempt,
    )
    payload["job_id"] = job.id
    result = _execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job.id,
            Job.status.in_((JOB_FAILED, JOB_CANCELLED)),
            Job.attempt == previous_attempt,
        )
        .values(
            status=JOB_QUEUED,
            attempt=next_attempt,
            progress=0,
            error_code=None,
            error_message=None,
            progress_data=payload,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.get(Job, job.id, populate_existing=True)
        current_status = current.status if current is not None else "missing"
        raise AppHTTPException(
            409,
            "JOB_NOT_RETRYABLE",
            f"Job cannot be retried because it is {current_status}.",
        )
    db.expire(job)
    return db.get(Job, job.id, populate_existing=True) or job
