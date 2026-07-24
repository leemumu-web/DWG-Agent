"""Workflow collection and detail query endpoints."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
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
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import WorkflowDetail, WorkflowRead
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException

collection_router = APIRouter()
detail_router = APIRouter()


@collection_router.get("/projects")
def list_workflow_projects(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Return a minimal project list for workflow filter dropdowns.

    Replaces the removed /projects CRUD endpoint.  Only active projects
    that the user can access are returned.
    """
    stmt = select(Project).where(Project.status == "active").order_by(Project.code)
    if not has_global_project_access(current_user):
        stmt = stmt.join(
            ProjectMember,
            ProjectMember.project_id == Project.id,
        ).where(ProjectMember.user_id == current_user.id)
    projects = db.scalars(stmt).all()
    return ok(
        [
            {"id": p.id, "code": p.code, "name": p.name}
            for p in projects
        ],
        request.state.request_id,
    )


@collection_router.get("")
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


@detail_router.get("/{workflow_id}")
def get_workflow(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    db.commit()
    return ok(WorkflowDetail.model_validate(workflow), request.state.request_id)
