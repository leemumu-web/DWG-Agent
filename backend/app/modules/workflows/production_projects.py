"""Atomic production-project application service."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.projects.interface import Project, ProjectCreate, create_project
from app.modules.workflows.lifecycle import create_workflow, start_workflow
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import ProductionProjectCreate, WorkflowCreate


@dataclass(frozen=True)
class ProductionProjectResult:
    project: Project
    workflow: WorkflowRun


def create_production_project(
    db: Session,
    payload: ProductionProjectCreate,
    *,
    created_by: int,
) -> ProductionProjectResult:
    """Create the project, its sole production workflow, and start it without committing."""
    project = create_project(
        db,
        ProjectCreate.model_validate(payload.model_dump()),
        owner_id=created_by,
    )
    workflow = create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name=f"{project.code} · {project.name}",
            workflow_type="linux_production",
        ),
        created_by=created_by,
    )
    start_workflow(db, workflow)
    db.flush()
    return ProductionProjectResult(project=project, workflow=workflow)
