from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    CurrentUser,
    get_db,
    has_global_project_access,
    require_project_member,
    require_project_role,
)
from app.core.exceptions import AppHTTPException
from app.db.pagination import paginate_scalars
from app.models.project import ProjectMember
from app.models.workflow import WorkflowRun
from app.schemas.common import ok
from app.schemas.common import page as page_response
from app.schemas.workflow_schema import WorkflowCreate, WorkflowDetail, WorkflowRead
from app.services.audit_service import write_audit_log
from app.services.workflow_service import (
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    get_workflow_or_404,
    start_workflow,
    sync_workflow_from_jobs,
)

router = APIRouter()
WORKFLOW_WRITE_ROLES = {"project_owner", "project_engineer"}
WORKFLOW_STATUSES = {
    "draft",
    "waiting_input",
    "running",
    "waiting_review",
    "succeeded",
    "failed",
    "cancelled",
}


def _load_detail(db: Session, workflow_id: int) -> WorkflowRun:
    workflow = db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == workflow_id)
        .options(selectinload(WorkflowRun.stages), selectinload(WorkflowRun.artifacts))
    )
    if workflow is None:
        return get_workflow_or_404(db, workflow_id)
    return workflow


@router.get("")
def list_workflows(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    project_id: int | None = Query(None, ge=1),
    workflow_status: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
    if project_id is not None:
        stmt = stmt.where(WorkflowRun.project_id == project_id)
    if workflow_status is not None:
        if workflow_status not in WORKFLOW_STATUSES:
            raise AppHTTPException(422, "INVALID_WORKFLOW_STATUS", "Invalid workflow status.")
        stmt = stmt.where(WorkflowRun.status == workflow_status)
    if not has_global_project_access(current_user):
        stmt = stmt.join(
            ProjectMember,
            ProjectMember.project_id == WorkflowRun.project_id,
        ).where(ProjectMember.user_id == current_user.id)
    workflows, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    return page_response(
        [WorkflowRead.model_validate(workflow) for workflow in workflows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_workflow_api(
    payload: WorkflowCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    require_project_role(db, current_user, payload.project_id, WORKFLOW_WRITE_ROLES)
    workflow = create_workflow(db, payload, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflows.create",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(WorkflowDetail.model_validate(_load_detail(db, workflow.id)), request.state.request_id)


@router.get("/{workflow_id}")
def get_workflow(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = _load_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    db.commit()
    return ok(WorkflowDetail.model_validate(workflow), request.state.request_id)


@router.post("/{workflow_id}/start")
def start_workflow_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = _load_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    start_workflow(db, workflow)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflows.start",
        resource_type="workflow",
        resource_id=workflow.id,
        request=request,
    )
    db.commit()
    return ok(WorkflowDetail.model_validate(workflow), request.state.request_id)


@router.post("/{workflow_id}/stages/{stage_code}/completion")
def complete_stage_api(
    workflow_id: int,
    stage_code: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = _load_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    complete_manual_stage(workflow, stage_code)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_stages.complete",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={"stage_code": stage_code},
        request=request,
    )
    db.commit()
    return ok(WorkflowDetail.model_validate(workflow), request.state.request_id)


@router.post("/{workflow_id}/cancellation-requests")
def cancel_workflow_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = _load_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    cancel_workflow(workflow)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflows.cancel",
        resource_type="workflow",
        resource_id=workflow.id,
        request=request,
    )
    db.commit()
    return ok(WorkflowDetail.model_validate(workflow), request.state.request_id)
