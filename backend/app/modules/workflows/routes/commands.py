"""Workflow creation, lifecycle transition and cancellation endpoints."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import Job
from app.modules.jobs.interface import cancel_job as transition_job_to_cancelled
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import (
    ProjectCreate,
    create_project,
    require_project_role,
)
from app.modules.workflows.access import WORKFLOW_WRITE_ROLES, load_workflow_detail
from app.modules.workflows.lifecycle import (
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    start_workflow,
)
from app.modules.workflows.schemas import WorkflowCreate, WorkflowDetail
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok

collection_router = APIRouter()
detail_router = APIRouter()


@collection_router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project_api(
    payload: ProjectCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Create a project for use in workflows.

    Requires admin or operator permission.  Viewer cannot create projects.
    """
    from app.modules.identity.access import has_any_role
    from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN

    if not has_any_role(current_user, {ROLE_SUPER_ADMIN, ROLE_ADMIN}):
        # Non-admin users need operator permission
        user_perms = {p.code for role in current_user.roles for p in role.permissions}
        if "operator" not in user_perms:
            from app.platform.http.exceptions import forbidden as _forbidden
            raise _forbidden("operator permission required to create projects")

    project = create_project(db, payload, owner_id=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_projects.create",
        resource_type="project",
        resource_id=project.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(
        {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "owner_id": project.owner_id,
            "owner_name": current_user.real_name,
        },
        request.state.request_id,
    )


@collection_router.post("", status_code=status.HTTP_201_CREATED)
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
    return ok(
        WorkflowDetail.model_validate(load_workflow_detail(db, workflow.id)),
        request.state.request_id,
    )


@detail_router.post("/{workflow_id}/start")
def start_workflow_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
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


@detail_router.post("/{workflow_id}/stages/{stage_code}/completion")
def complete_stage_api(
    workflow_id: int,
    stage_code: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    complete_manual_stage(db, workflow, stage_code)
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


@detail_router.post("/{workflow_id}/cancellation-requests")
def cancel_workflow_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    current_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == workflow.current_stage),
        None,
    )
    if current_stage is not None and current_stage.job_id is not None:
        job = db.get(Job, current_stage.job_id)
        if job is not None and job.status in {
            "pending",
            "queued",
            "running",
            "validating",
            "waiting_cad_worker",
        }:
            transition_job_to_cancelled(db, job)
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
