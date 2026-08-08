"""Workflow collection and detail query endpoints."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.modules.identity.interface import CurrentUser
from app.modules.projects.interface import (
    Project,
    ProjectMember,
    has_global_project_access,
    require_project_member,
)
from app.modules.workflows.access import (
    WORKFLOW_STATUSES,
    load_workflow_detail,
)
from app.modules.workflows.job_sync import sync_workflow_from_jobs, workflow_needs_sync
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import WORKFLOW_TYPES, WorkflowDetail, WorkflowRead
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException

collection_router = APIRouter()
detail_router = APIRouter()


@collection_router.get("")
def list_workflows(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    project_id: int | None = Query(None, ge=1),
    workflow_type: str | None = Query(None),
    workflow_status: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    scope = select(WorkflowRun)
    if project_id is not None:
        scope = scope.where(WorkflowRun.project_id == project_id)
    if workflow_type is not None:
        if workflow_type not in WORKFLOW_TYPES:
            raise AppHTTPException(422, "INVALID_WORKFLOW_TYPE", "Invalid workflow type.")
        scope = scope.where(WorkflowRun.workflow_type == workflow_type)
    if not has_global_project_access(current_user):
        scope = scope.join(
            ProjectMember,
            ProjectMember.project_id == WorkflowRun.project_id,
        ).where(ProjectMember.user_id == current_user.id)
    summary_scope = scope.with_only_columns(
        func.count(WorkflowRun.id),
        func.coalesce(
            func.sum(case((WorkflowRun.status == "running", 1), else_=0)),
            0,
        ),
        func.coalesce(
            func.sum(
                case(
                    (WorkflowRun.status.in_(("waiting_input", "waiting_review")), 1),
                    else_=0,
                )
            ),
            0,
        ),
        func.coalesce(
            func.sum(case((WorkflowRun.status == "succeeded", 1), else_=0)),
            0,
        ),
    ).order_by(None)
    summary_row = db.execute(summary_scope).one()
    if workflow_status is not None:
        if workflow_status not in WORKFLOW_STATUSES:
            raise AppHTTPException(422, "INVALID_WORKFLOW_STATUS", "Invalid workflow status.")
        scope = scope.where(WorkflowRun.status == workflow_status)
    stmt = scope.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
    workflows, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    projects = {
        project.id: project
        for project in db.scalars(
            select(Project).where(
                Project.id.in_({workflow.project_id for workflow in workflows})
            )
        ).all()
    }
    response = page_response(
        [
            WorkflowRead.model_validate(workflow).model_copy(
                update={
                    "project_code": projects[workflow.project_id].code,
                    "project_name": projects[workflow.project_id].name,
                }
            )
            for workflow in workflows
        ],
        page,
        page_size,
        total,
        request.state.request_id,
    )
    response["summary"] = {
        "total": int(summary_row[0]),
        "running": int(summary_row[1]),
        "waiting": int(summary_row[2]),
        "completed": int(summary_row[3]),
    }
    return response


@detail_router.get("/{workflow_id}")
def get_workflow(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    if workflow_needs_sync(db, workflow):
        sync_workflow_from_jobs(db, workflow)
        db.commit()
    return ok(WorkflowDetail.model_validate(workflow), request.state.request_id)
