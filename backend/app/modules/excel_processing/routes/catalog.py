"""Read-only Excel Final relationship catalog endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.excel_processing.access import get_accessible_batch
from app.modules.excel_processing.models import (
    ExcelFinalBatch,
    ExcelFinalComponent,
    ExcelFinalPart,
)
from app.modules.excel_processing.presentation import (
    batch_detail,
    batch_summary,
    component_item,
    part_catalog_item,
    part_detail,
    part_search_item,
)
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import Job, job_read_filter
from app.modules.projects.interface import has_global_project_access
from app.platform.config.constants import TASK_EXCEL_FINAL
from app.platform.database.pagination import paginate_scalars
from app.platform.database.session import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import not_found

static_router = APIRouter()
item_router = APIRouter()


def _readable_batch_statement(current_user: CurrentUser):
    statement = (
        select(ExcelFinalBatch)
        .join(Job, Job.id == ExcelFinalBatch.job_id)
        .where(Job.task_type == TASK_EXCEL_FINAL)
    )
    if not has_global_project_access(current_user):
        statement = statement.where(job_read_filter(current_user))
    return statement


@static_router.get("/overview")
def get_overview(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Return exact aggregate totals for every readable Excel Final batch."""
    statement = select(
        func.count(ExcelFinalBatch.id),
        func.sum(ExcelFinalBatch.part_count),
        func.sum(ExcelFinalBatch.component_count),
        func.sum(ExcelFinalBatch.total_net_weight),
        func.sum(ExcelFinalBatch.total_gross_weight),
        func.max(ExcelFinalBatch.created_at),
    ).join(Job, Job.id == ExcelFinalBatch.job_id)
    statement = statement.where(Job.task_type == TASK_EXCEL_FINAL)
    if not has_global_project_access(current_user):
        statement = statement.where(job_read_filter(current_user))
    row = db.execute(statement).one()
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


@static_router.get("/batches")
def list_batches(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """列出当前用户可读的处理批次（最近优先）。"""
    statement = _readable_batch_statement(current_user).order_by(ExcelFinalBatch.id.desc())
    batches, total = paginate_scalars(db, statement, page_no=page, page_size=page_size)
    return page_response(
        [batch_summary(batch) for batch in batches],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@static_router.get("/parts/search")
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
    """在当前用户可读批次中搜索零件。"""
    statement = (
        select(ExcelFinalPart)
        .join(ExcelFinalBatch, ExcelFinalBatch.id == ExcelFinalPart.batch_id)
        .join(Job, Job.id == ExcelFinalBatch.job_id)
        .where(Job.task_type == TASK_EXCEL_FINAL)
    )
    if not has_global_project_access(current_user):
        statement = statement.where(job_read_filter(current_user))
    if spec.strip():
        statement = statement.where(ExcelFinalPart.spec.contains(spec.strip()))
    if material.strip():
        statement = statement.where(ExcelFinalPart.material == material.strip())
    if part_no.strip():
        statement = statement.where(ExcelFinalPart.part_no.contains(part_no.strip()))
    statement = statement.order_by(ExcelFinalPart.id.desc())
    parts, total = paginate_scalars(db, statement, page_no=page, page_size=page_size)
    return page_response(
        [part_search_item(part) for part in parts],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@item_router.get("/batches/{batch_id}")
def get_batch_detail(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """返回批次摘要、材质分布和高频规格。"""
    batch = get_accessible_batch(db, current_user, batch_id)
    material_stats = db.execute(
        select(
            ExcelFinalPart.material,
            func.count(ExcelFinalPart.id),
            func.sum(ExcelFinalPart.net_total_weight),
        )
        .where(
            ExcelFinalPart.batch_id == batch_id,
            ExcelFinalPart.material.is_not(None),
        )
        .group_by(ExcelFinalPart.material)
    ).all()
    spec_stats = db.execute(
        select(ExcelFinalPart.spec, func.count(ExcelFinalPart.id))
        .where(
            ExcelFinalPart.batch_id == batch_id,
            ExcelFinalPart.spec.is_not(None),
        )
        .group_by(ExcelFinalPart.spec)
        .order_by(func.count(ExcelFinalPart.id).desc())
        .limit(20)
    ).all()
    return ok(
        batch_detail(batch, material_stats=material_stats, spec_stats=spec_stats),
        request.state.request_id,
    )


@item_router.get("/batches/{batch_id}/parts")
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
    """批次下零件列表（服务端分页与筛选）。"""
    get_accessible_batch(db, current_user, batch_id)
    statement = select(ExcelFinalPart).where(ExcelFinalPart.batch_id == batch_id)
    if spec.strip():
        statement = statement.where(ExcelFinalPart.spec.contains(spec.strip()))
    if material.strip():
        statement = statement.where(ExcelFinalPart.material == material.strip())
    if part_no.strip():
        statement = statement.where(ExcelFinalPart.part_no.contains(part_no.strip()))
    if part_type.strip():
        statement = statement.where(ExcelFinalPart.part_type == part_type.strip())
    statement = statement.order_by(ExcelFinalPart.seq)
    parts, total = paginate_scalars(db, statement, page_no=page, page_size=page_size)
    return page_response(
        [part_catalog_item(part) for part in parts],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@item_router.get("/batches/{batch_id}/parts/{part_id}")
def get_part_detail(
    batch_id: int,
    part_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """返回批次内单个零件的完整关系化字段。"""
    get_accessible_batch(db, current_user, batch_id)
    part = db.scalar(
        select(ExcelFinalPart).where(
            ExcelFinalPart.id == part_id,
            ExcelFinalPart.batch_id == batch_id,
        )
    )
    if not part:
        raise not_found("Part")
    return ok(part_detail(part), request.state.request_id)


@item_router.get("/batches/{batch_id}/components")
def list_batch_components(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """批次下构件汇总列表（服务端分页）。"""
    get_accessible_batch(db, current_user, batch_id)
    statement = (
        select(ExcelFinalComponent)
        .where(ExcelFinalComponent.batch_id == batch_id)
        .order_by(ExcelFinalComponent.id)
    )
    components, total = paginate_scalars(
        db,
        statement,
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [component_item(component) for component in components],
        page,
        page_size,
        total,
        request.state.request_id,
    )


__all__ = ["item_router", "static_router"]
