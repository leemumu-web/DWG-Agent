"""Excel→Final Part-List API — 面向前端的便捷端点。

提供上传、处理、MySQL 数据查询、比重查询、健康检查。
所有端点需认证。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, has_global_project_access
from app.core.config import settings
from app.core.constants import TASK_EXCEL_FINAL
from app.core.exceptions import AppHTTPException, forbidden, not_found, service_unavailable
from app.db.pagination import paginate_scalars
from app.integrations.excel_final import (
    ExcelFinalIntegrationError,
    excel_final_dependencies_available,
    get_excel_final_stage_root,
    handbook_database_available,
    lookup_excel_final_weight,
)
from app.models.excel_final import ExcelFinalBatch, ExcelFinalComponent, ExcelFinalPart
from app.models.file import StoredFile
from app.models.job import Job
from app.models.result import AnalysisResult
from app.schemas.common import ok
from app.schemas.common import page as page_response
from app.services.audit_service import write_audit_log
from app.services.file_service import build_signed_download_url
from app.services.file_transfer_service import (
    ACTIVE_TRANSFER_STATUSES,
    TransferSpec,
    complete_transfer_in_transaction,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_transfer,
)
from app.services.job_access import job_read_filter, require_job_read_access
from app.services.job_service import create_or_reuse_job, dispatch_committed_job
from app.services.storage_service import sanitize_filename, save_upload_file

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────


def _ensure_pipeline_enabled() -> None:
    if not settings.excel_final_pipeline_enabled:
        raise service_unavailable(
            "EXCEL_FINAL_PIPELINE_DISABLED",
            "Excel→Final pipeline is disabled. Set EXCEL_FINAL_PIPELINE_ENABLED=true to enable.",
        )


def _scoped_request_key(endpoint: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 96
        or re.fullmatch(r"[A-Za-z0-9._:-]+", normalized) is None
    ):
        raise AppHTTPException(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key has an invalid format.",
        )
    return f"{endpoint}:{normalized}"


def _require_input_file_access(current_user: CurrentUser, stored: StoredFile) -> None:
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return
    raise forbidden("Only the file uploader or an administrator can process this file.")


def _get_excel_job(db: Session, current_user: CurrentUser, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job or job.task_type != TASK_EXCEL_FINAL:
        raise not_found("Excel Final job")
    require_job_read_access(db, current_user, job)
    return job


def _get_accessible_batch(db: Session, current_user: CurrentUser, batch_id: int) -> ExcelFinalBatch:
    batch = db.get(ExcelFinalBatch, batch_id)
    if not batch:
        raise not_found("Batch")
    job = db.get(Job, batch.job_id)
    if not job or job.task_type != TASK_EXCEL_FINAL:
        raise not_found("Excel Final job")
    require_job_read_access(db, current_user, job)
    return batch


async def _store_excel_upload(
    db: Session,
    upload: UploadFile,
    *,
    current_user: CurrentUser,
    request: Request,
    idempotency_key: str | None = None,
) -> tuple[StoredFile, bool]:
    """Persist an Excel upload with the same durable saga used by /files."""
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="inbound",
            operation="upload",
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
            original_name=sanitize_filename(upload.filename or "unnamed.xlsx"),
        ),
    )
    # End the authentication/read snapshot before the independent transfer
    # session advances this durable intent to in_progress.
    db.commit()
    if transfer.status == "succeeded" and transfer.file_id is not None:
        stored = db.get(StoredFile, transfer.file_id)
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "IDEMPOTENT_RESULT_MISSING",
                "The previous upload result is no longer available.",
            )
        return stored, True
    if transfer.status not in ACTIVE_TRANSFER_STATUSES:
        raise AppHTTPException(
            409,
            "IDEMPOTENT_OPERATION_FAILED",
            "The previous upload with this idempotency key did not succeed.",
            {"transfer_uid": transfer.transfer_uid},
        )
    try:
        stored = await save_upload_file(
            db,
            upload,
            uploaded_by=current_user.id,
            transfer_uid=transfer.transfer_uid,
            request_id=request.state.request_id,
        )
        complete_transfer_in_transaction(
            db,
            transfer.transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
        return stored, False
    except Exception as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc, AppHTTPException) else None
        settle_transfer(
            session_factory_for(db),
            transfer.transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code=(
                detail["code"] if isinstance(detail, dict) else "UPLOAD_TRANSACTION_FAILED"
            ),
            error_message=(
                detail["message"]
                if isinstance(detail, dict)
                else "Upload transaction failed before completion."
            ),
        )
        raise


# ── Upload & Process ─────────────────────────────────────────────────


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_excel(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传 .xlsx/.xls 文件到 MinIO (dwg-reports bucket)。"""
    _ensure_pipeline_enabled()
    stored, _upload_reused = await _store_excel_upload(
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


@router.post("/process", status_code=status.HTTP_202_ACCEPTED)
def process_file(
    request: Request,
    current_user: CurrentUser,
    file_id: int = Query(..., ge=1, description="已上传的 Excel 文件 ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """提交 excel_final 处理请求，返回 job_id 供轮询。"""
    _ensure_pipeline_enabled()

    sfile = db.get(StoredFile, file_id)
    if not sfile or sfile.status == "deleted":
        raise not_found("File")
    _require_input_file_access(current_user, sfile)

    from app.schemas.job_schema import JobCreate

    payload = JobCreate(
        task_type=TASK_EXCEL_FINAL,
        params={"file_id": file_id},
    )
    job, reused = create_or_reuse_job(
        db,
        payload,
        created_by=current_user.id,
        request_key=_scoped_request_key("process", idempotency_key),
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
    db.commit()
    if not reused:
        dispatch_committed_job(db, job)
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


@router.post("/upload-and-process", status_code=status.HTTP_202_ACCEPTED)
async def upload_and_process(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """上传受支持的钢构清单 → 存储 → 创建作业 → 返回 job_id。"""
    _ensure_pipeline_enabled()

    scoped_key = _scoped_request_key("upload-and-process", idempotency_key)
    if scoped_key is not None:
        existing_job = db.scalar(
            select(Job).where(
                Job.created_by == current_user.id,
                Job.task_type == TASK_EXCEL_FINAL,
                Job.request_key == scoped_key,
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

    stored, _upload_reused = await _store_excel_upload(
        db,
        upload,
        current_user=current_user,
        request=request,
        idempotency_key=scoped_key,
    )
    db.flush()

    from app.schemas.job_schema import JobCreate

    payload = JobCreate(
        task_type=TASK_EXCEL_FINAL,
        params={"file_id": stored.id},
    )
    job, reused = create_or_reuse_job(
        db,
        payload,
        created_by=current_user.id,
        request_key=scoped_key,
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
    db.commit()
    if not reused:
        dispatch_committed_job(db, job)
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


@router.get("/process/{job_id}")
def get_process_status(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """查询 excel_final 处理任务状态 + 结果摘要。"""
    job = _get_excel_job(db, current_user, job_id)

    # Get batch info if completed
    batch_info = None
    if job.status == "succeeded":
        batch = db.scalar(select(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job_id))
        if batch:
            batch_info = {
                "batch_id": batch.id,
                "source_type": batch.source_type,
                "source_name": batch.source_name,
                "part_count": batch.part_count,
                "component_count": batch.component_count,
                "total_net_weight": batch.total_net_weight,
                "total_gross_weight": batch.total_gross_weight,
            }

    # Get result file for download
    result_file_id = None
    if job.status == "succeeded":
        result = db.scalar(
            select(AnalysisResult)
            .where(AnalysisResult.job_id == job_id)
            .order_by(AnalysisResult.id.desc())
            .limit(1)
        )
        if result:
            result_file_id = result.result_file_id

    return ok(
        {
            "job_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "pipeline": job.pipeline,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "batch": batch_info,
            "result_file_id": result_file_id,
        },
        request.state.request_id,
    )


@router.get("/process/{job_id}/download")
def download_result(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """下载 excel_final 处理结果 Excel 文件。"""
    _get_excel_job(db, current_user, job_id)

    result = db.scalar(
        select(AnalysisResult)
        .where(AnalysisResult.job_id == job_id)
        .order_by(AnalysisResult.id.desc())
        .limit(1)
    )
    if not result or not result.result_file_id:
        raise not_found("Result file")

    download_info = build_signed_download_url(result.result_file_id)
    return ok(download_info, request.state.request_id)


# ── MySQL Data Queries ───────────────────────────────────────────────


@router.get("/overview")
def get_overview(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Return exact aggregate totals for every Excel Final batch the user can read."""
    stmt = select(
        func.count(ExcelFinalBatch.id),
        func.sum(ExcelFinalBatch.part_count),
        func.sum(ExcelFinalBatch.component_count),
        func.sum(ExcelFinalBatch.total_net_weight),
        func.sum(ExcelFinalBatch.total_gross_weight),
        func.max(ExcelFinalBatch.created_at),
    ).join(Job, Job.id == ExcelFinalBatch.job_id)
    if not has_global_project_access(current_user):
        stmt = stmt.where(job_read_filter(current_user))
    row = db.execute(stmt).one()
    latest_created_at = row[5]
    return ok(
        {
            "batch_count": int(row[0] or 0),
            "part_count": int(row[1] or 0),
            "component_count": int(row[2] or 0),
            "total_net_weight": float(row[3] or 0.0),
            "total_gross_weight": float(row[4] or 0.0),
            "latest_created_at": (
                latest_created_at.isoformat() if latest_created_at is not None else None
            ),
        },
        request.state.request_id,
    )


@router.get("/batches")
def list_batches(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """列出所有处理批次（最近优先）。"""
    stmt = select(ExcelFinalBatch).join(Job, Job.id == ExcelFinalBatch.job_id)
    if not has_global_project_access(current_user):
        stmt = stmt.where(job_read_filter(current_user))
    stmt = stmt.order_by(ExcelFinalBatch.id.desc())
    batches, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    data = [
        {
            "batch_id": b.id,
            "job_id": b.job_id,
            "file_id": b.file_id,
            "source_type": b.source_type,
            "source_name": b.source_name,
            "part_count": b.part_count,
            "component_count": b.component_count,
            "total_net_weight": b.total_net_weight,
            "total_gross_weight": b.total_gross_weight,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in batches
    ]
    return page_response(data, page, page_size, total, request.state.request_id)


@router.get("/batches/{batch_id}")
def get_batch_detail(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """批次详情：构件汇总 + 统计。"""
    batch = _get_accessible_batch(db, current_user, batch_id)

    # Aggregate stats
    from sqlalchemy import func as sa_func

    material_stats = list(
        db.execute(
            select(
                ExcelFinalPart.material,
                sa_func.count(ExcelFinalPart.id),
                sa_func.sum(ExcelFinalPart.net_total_weight),
            )
            .where(ExcelFinalPart.batch_id == batch_id, ExcelFinalPart.material.is_not(None))
            .group_by(ExcelFinalPart.material)
        ).all()
    )

    spec_stats = list(
        db.execute(
            select(
                ExcelFinalPart.spec,
                sa_func.count(ExcelFinalPart.id),
            )
            .where(ExcelFinalPart.batch_id == batch_id, ExcelFinalPart.spec.is_not(None))
            .group_by(ExcelFinalPart.spec)
            .order_by(sa_func.count(ExcelFinalPart.id).desc())
            .limit(20)
        ).all()
    )

    return ok(
        {
            "batch_id": batch.id,
            "job_id": batch.job_id,
            "file_id": batch.file_id,
            "source_type": batch.source_type,
            "source_name": batch.source_name,
            "part_count": batch.part_count,
            "component_count": batch.component_count,
            "total_net_weight": batch.total_net_weight,
            "total_gross_weight": batch.total_gross_weight,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "material_breakdown": [
                {"material": m, "count": c, "total_net_weight": float(w) if w else None}
                for m, c, w in material_stats
            ],
            "top_specs": [{"spec": s, "count": c} for s, c in spec_stats],
        },
        request.state.request_id,
    )


@router.get("/batches/{batch_id}/parts")
def list_batch_parts(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    spec: str = Query("", description="按规格筛选"),
    material: str = Query("", description="按材质筛选"),
    part_no: str = Query("", description="按零件号筛选"),
    part_type: str = Query("", description="按类型筛选 (e.g. 零件, BH腹, BH翼)"),
    db: Session = Depends(get_db),
):
    """批次下零件列表（分页 + 筛选）。"""
    _get_accessible_batch(db, current_user, batch_id)

    stmt = select(ExcelFinalPart).where(ExcelFinalPart.batch_id == batch_id)
    if spec.strip():
        stmt = stmt.where(ExcelFinalPart.spec.contains(spec.strip()))
    if material.strip():
        stmt = stmt.where(ExcelFinalPart.material == material.strip())
    if part_no.strip():
        stmt = stmt.where(ExcelFinalPart.part_no.contains(part_no.strip()))
    if part_type.strip():
        stmt = stmt.where(ExcelFinalPart.part_type == part_type.strip())

    stmt = stmt.order_by(ExcelFinalPart.seq)
    parts, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)

    data = [
        {
            "id": p.id,
            "seq": p.seq,
            "component_no": p.component_no,
            "component_qty": p.component_qty,
            "part_type": p.part_type,
            "part_no": p.part_no,
            "profile_spec": p.profile_spec,
            "spec": p.spec,
            "width": p.width,
            "length": p.length,
            "cut_length": p.cut_length,
            "material": p.material,
            "qty": p.qty,
            "total_qty": p.total_qty,
            "total_length": p.total_length,
            "density": p.density,
            "theo_unit_weight": p.theo_unit_weight,
            "theo_total_weight": p.theo_total_weight,
            "net_unit_weight": p.net_unit_weight,
            "net_total_weight": p.net_total_weight,
            "table_net_weight": p.table_net_weight,
            "gross_unit_weight": p.gross_unit_weight,
            "gross_total_weight": p.gross_total_weight,
            "table_gross_weight": p.table_gross_weight,
            "surface_area": p.surface_area,
            "total_surface_area": p.total_surface_area,
        }
        for p in parts
    ]
    return page_response(data, page, page_size, total, request.state.request_id)


@router.get("/batches/{batch_id}/parts/{part_id}")
def get_part_detail(
    batch_id: int,
    part_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """单个零件详情。"""
    _get_accessible_batch(db, current_user, batch_id)
    part = db.scalar(
        select(ExcelFinalPart).where(
            ExcelFinalPart.id == part_id,
            ExcelFinalPart.batch_id == batch_id,
        )
    )
    if not part:
        raise not_found("Part")

    return ok(
        {
            "id": part.id,
            "batch_id": part.batch_id,
            "seq": part.seq,
            "component_no": part.component_no,
            "component_qty": part.component_qty,
            "part_type": part.part_type,
            "part_no": part.part_no,
            "profile_spec": part.profile_spec,
            "spec": part.spec,
            "width": part.width,
            "length": part.length,
            "left_inset": part.left_inset,
            "right_inset": part.right_inset,
            "cut_length": part.cut_length,
            "material": part.material,
            "qty": part.qty,
            "total_qty": part.total_qty,
            "total_length": part.total_length,
            "density": part.density,
            "theo_unit_weight": part.theo_unit_weight,
            "theo_total_weight": part.theo_total_weight,
            "net_unit_weight": part.net_unit_weight,
            "net_total_weight": part.net_total_weight,
            "table_net_weight": part.table_net_weight,
            "gross_unit_weight": part.gross_unit_weight,
            "gross_total_weight": part.gross_total_weight,
            "table_gross_weight": part.table_gross_weight,
            "surface_area": part.surface_area,
            "total_surface_area": part.total_surface_area,
            "created_at": part.created_at.isoformat() if part.created_at else None,
        },
        request.state.request_id,
    )


@router.get("/batches/{batch_id}/components")
def list_batch_components(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """批次下构件汇总列表（服务端分页）。"""
    _get_accessible_batch(db, current_user, batch_id)

    stmt = (
        select(ExcelFinalComponent)
        .where(ExcelFinalComponent.batch_id == batch_id)
        .order_by(ExcelFinalComponent.id)
    )
    comps, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    data = [
        {
            "id": c.id,
            "component_no": c.component_no,
            "component_qty": c.component_qty,
            "total_weight": c.total_weight,
        }
        for c in comps
    ]
    return page_response(data, page, page_size, total, request.state.request_id)


@router.get("/parts/search")
def search_parts(
    request: Request,
    current_user: CurrentUser,
    spec: str = Query("", description="规格关键词"),
    material: str = Query("", description="材质"),
    part_no: str = Query("", description="零件号"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """跨批次零件搜索。"""
    stmt = (
        select(ExcelFinalPart)
        .join(ExcelFinalBatch, ExcelFinalBatch.id == ExcelFinalPart.batch_id)
        .join(Job, Job.id == ExcelFinalBatch.job_id)
    )
    if not has_global_project_access(current_user):
        stmt = stmt.where(job_read_filter(current_user))
    if spec.strip():
        stmt = stmt.where(ExcelFinalPart.spec.contains(spec.strip()))
    if material.strip():
        stmt = stmt.where(ExcelFinalPart.material == material.strip())
    if part_no.strip():
        stmt = stmt.where(ExcelFinalPart.part_no.contains(part_no.strip()))

    stmt = stmt.order_by(ExcelFinalPart.id.desc())
    parts, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)

    data = [
        {
            "id": p.id,
            "batch_id": p.batch_id,
            "seq": p.seq,
            "component_no": p.component_no,
            "part_type": p.part_type,
            "part_no": p.part_no,
            "spec": p.spec,
            "width": p.width,
            "length": p.length,
            "material": p.material,
            "qty": p.qty,
            "net_total_weight": p.net_total_weight,
            "theo_total_weight": p.theo_total_weight,
        }
        for p in parts
    ]
    return page_response(data, page, page_size, total, request.state.request_id)


# ── Tools ────────────────────────────────────────────────────────────


@router.get("/weights/lookup")
def lookup_weight(
    request: Request,
    current_user: CurrentUser,
    spec: str = Query(..., min_length=1, description="钢材规格, e.g. L50x5, φ60*3.5, PL10*200"),
):
    """五金手册比重查询 (kg/m)。依赖 hardware_handbook MySQL。"""
    try:
        weight, source = lookup_excel_final_weight(spec)
        return ok(
            {
                "spec": spec,
                "weight_kg_per_m": weight,
                "source": source,
            },
            request.state.request_id,
        )
    except ExcelFinalIntegrationError as exc:
        raise service_unavailable(
            "EXCEL_FINAL_UNAVAILABLE",
            "Excel Final 比重查询暂不可用。",
        ) from exc


@router.get("/health")
def health_check(
    request: Request,
    current_user: CurrentUser,
):
    """检查 excel_final 流水线是否可用。"""
    is_enabled = settings.excel_final_pipeline_enabled
    try:
        stage_root = get_excel_final_stage_root()
        stage_available = True
    except ExcelFinalIntegrationError:
        stage_root = None
        stage_available = False

    dependencies_available = excel_final_dependencies_available()
    pkg_available = stage_available and dependencies_available
    handbook_available = bool(stage_root and (stage_root / "handbook.py").is_file())
    if handbook_available:
        handbook_db_available = handbook_database_available()
    else:
        handbook_db_available = False

    if not stage_available:
        pkg_available = False

    return ok(
        {
            "pipeline_enabled": is_enabled,
            "stage_available": stage_available,
            "dependencies_available": dependencies_available,
            "package_available": pkg_available,
            "handbook_available": handbook_available,
            "handbook_database_available": handbook_db_available,
            "ready": is_enabled and pkg_available and handbook_db_available,
        },
        request.state.request_id,
    )
