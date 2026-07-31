"""Upload, submission, status and download endpoints for Excel Final."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.excel_processing.access import (
    get_excel_job,
    require_input_file_access,
)
from app.modules.excel_processing.availability import ensure_pipeline_enabled
from app.modules.excel_processing.idempotency import scoped_request_key
from app.modules.excel_processing.models import ExcelFinalBatch
from app.modules.excel_processing.presentation import process_status
from app.modules.excel_processing.uploads import store_excel_upload
from app.modules.excel_processing.validation import (
    preflight_excel_upload,
    preflight_stored_excel,
)
from app.modules.files.interface import StoredFile, build_signed_download_url
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    JobCreate,
    create_or_reuse_job,
    drain_eager_dispatches,
    stage_job_dispatch,
)
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import EXCEL_FILE_EXTENSIONS, TASK_EXCEL_FINAL
from app.platform.database.session import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, not_found

static_router = APIRouter()
item_router = APIRouter()


@static_router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_excel(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传 .xlsx/.xls 文件并使用 files 事务流水登记对象。"""
    ensure_pipeline_enabled()
    await preflight_excel_upload(upload)
    stored, _reused = await store_excel_upload(
        db,
        upload,
        current_user=current_user,
        request=request,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="excel_final.upload",
        resource_type="file",
        resource_id=stored.id,
        after_json={"original_name": stored.original_name},
        request=request,
    )
    db.commit()
    return ok(
        {
            "file_id": stored.id,
            "original_name": stored.original_name,
            "file_ext": stored.file_ext,
            "size_bytes": stored.size_bytes,
            "bucket": stored.bucket,
        },
        request.state.request_id,
    )


@static_router.post("/process", status_code=status.HTTP_202_ACCEPTED)
def process_file(
    request: Request,
    current_user: CurrentUser,
    file_id: int = Query(..., ge=1, description="已上传的 Excel 文件 ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """提交 Excel Final 任务，返回可轮询的 Job。"""
    ensure_pipeline_enabled()
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    require_input_file_access(current_user, stored)
    if (stored.file_ext or "").lower() not in EXCEL_FILE_EXTENSIONS:
        raise AppHTTPException(
            415, "NOT_EXCEL", "Only .xls, .xlsx or .xlsm files can be processed."
        )
    preflight_stored_excel(stored)

    job, reused = create_or_reuse_job(
        db,
        JobCreate(task_type=TASK_EXCEL_FINAL, params={"file_id": file_id}),
        created_by=current_user.id,
        request_key=scoped_request_key("process", idempotency_key),
    )
    if not reused:
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="excel_final.process",
            resource_type="job",
            resource_id=job.id,
            after_json={"file_id": file_id},
            request=request,
        )
        stage_job_dispatch(db, job)
    db.commit()
    if not reused:
        drain_eager_dispatches(db)
    return ok(
        {
            "job_id": job.id,
            "file_id": file_id,
            "status": job.status,
            "reused": reused,
            "message": "处理任务已入队，请轮询 GET /excel-final/process/{job_id} 获取进度",
        },
        request.state.request_id,
    )


@static_router.post("/upload-and-process", status_code=status.HTTP_202_ACCEPTED)
async def upload_and_process(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """上传、登记并提交一个 Excel Final Job。"""
    ensure_pipeline_enabled()
    request_key = scoped_request_key("upload-and-process", idempotency_key)
    if request_key is not None:
        existing_job = db.scalar(
            select(Job).where(
                Job.created_by == current_user.id,
                Job.task_type == TASK_EXCEL_FINAL,
                Job.request_key == request_key,
            )
        )
        if existing_job is not None:
            existing_file_id = (existing_job.params_json or {}).get("file_id")
            existing_file = (
                db.get(StoredFile, existing_file_id)
                if isinstance(existing_file_id, int)
                else None
            )
            if existing_file is None or existing_file.status == "deleted":
                raise AppHTTPException(
                    409,
                    "IDEMPOTENT_RESULT_MISSING",
                    "The previous upload result is no longer available.",
                )
            return ok(
                {
                    "job_id": existing_job.id,
                    "file_id": existing_file.id,
                    "original_name": existing_file.original_name,
                    "status": existing_job.status,
                    "reused": True,
                    "message": "已复用先前登记的处理任务",
                },
                request.state.request_id,
            )

    await preflight_excel_upload(upload)
    stored, _upload_reused = await store_excel_upload(
        db,
        upload,
        current_user=current_user,
        request=request,
        idempotency_key=request_key,
    )
    db.flush()
    job, reused = create_or_reuse_job(
        db,
        JobCreate(task_type=TASK_EXCEL_FINAL, params={"file_id": stored.id}),
        created_by=current_user.id,
        request_key=request_key,
    )
    if not reused:
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="excel_final.upload_and_process",
            resource_type="job",
            resource_id=job.id,
            after_json={"file_id": stored.id, "original_name": stored.original_name},
            request=request,
        )
        stage_job_dispatch(db, job)
    db.commit()
    if not reused:
        drain_eager_dispatches(db)
    return ok(
        {
            "job_id": job.id,
            "file_id": stored.id,
            "original_name": stored.original_name,
            "status": job.status,
            "reused": reused,
            "message": "文件已上传，处理任务已入队",
        },
        request.state.request_id,
    )


@item_router.get("/process/{job_id}")
def get_process_status(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """查询 Job 状态和已完成的关系批次/结果文件摘要。"""
    job = get_excel_job(db, current_user, job_id)
    batch = None
    result_file_id = None
    if job.status == "succeeded":
        batch = db.scalar(select(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job_id))
        result = db.scalar(
            select(AnalysisResult)
            .where(AnalysisResult.job_id == job_id)
            .order_by(AnalysisResult.id.desc())
            .limit(1)
        )
        if result:
            result_file_id = result.result_file_id
    return ok(
        process_status(job, batch=batch, result_file_id=result_file_id),
        request.state.request_id,
    )


@item_router.get("/process/{job_id}/download")
def download_result(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """生成当前用户可访问的 Excel Final 结果下载地址。"""
    get_excel_job(db, current_user, job_id)
    result = db.scalar(
        select(AnalysisResult)
        .where(AnalysisResult.job_id == job_id)
        .order_by(AnalysisResult.id.desc())
        .limit(1)
    )
    if not result or not result.result_file_id:
        raise not_found("Result file")
    return ok(build_signed_download_url(result.result_file_id), request.state.request_id)


__all__ = ["item_router", "static_router"]
