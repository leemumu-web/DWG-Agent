"""Atomic production-project HTTP entry point."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import ProjectRead
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.production_projects import create_production_project
from app.modules.workflows.schemas import (
    ProductionProjectCreate,
    ProductionProjectEnvelope,
    ProductionProjectRead,
    WorkflowDetail,
)
from app.platform.config.constants import ROLE_OPERATOR
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok

router = APIRouter()


@router.post(
    "/production-projects",
    status_code=status.HTTP_201_CREATED,
    response_model=ProductionProjectEnvelope,
    summary="创建生产项目及其唯一完整工作流",
)
def create_production_project_api(
    payload: ProductionProjectCreate,
    request: Request,
    current_user: User = Depends(require_roles(ROLE_OPERATOR)),
    db: Session = Depends(get_db),
):
    result = create_production_project(db, payload, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="projects.create",
        resource_type="project",
        resource_id=result.project.id,
        after_json=payload.model_dump(),
        request=request,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflows.create",
        resource_type="workflow",
        resource_id=result.workflow.id,
        after_json={
            "project_id": result.project.id,
            "workflow_type": "linux_production",
            "atomic_creation": True,
        },
        request=request,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflows.start",
        resource_type="workflow",
        resource_id=result.workflow.id,
        after_json={"project_id": result.project.id, "atomic_creation": True},
        request=request,
    )
    db.commit()
    project = ProjectRead.model_validate(result.project)
    workflow = WorkflowDetail.model_validate(load_workflow_detail(db, result.workflow.id))
    return ok(
        ProductionProjectRead(project=project, workflow=workflow).model_dump(),
        request.state.request_id,
    )
