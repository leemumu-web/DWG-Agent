from __future__ import annotations

from app.models.project import Project, ProjectMember
from app.models.user import User
from app.schemas.workflow_schema import WorkflowCreate
from app.services.workflow_service import (
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    start_workflow,
)


def _owner_project(db):
    user = User(username="workflow-owner", password_hash="x", real_name="Workflow Owner", status="active")
    db.add(user)
    db.flush()
    project = Project(code="WF-001", name="Workflow Project", owner_id=user.id, status="active")
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, project_role="project_owner"))
    db.flush()
    return user, project


def test_excel_delivery_workflow_has_bounded_non_cad_stages(db):
    user, project = _owner_project(db)
    workflow = create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Excel delivery", workflow_type="excel_delivery"),
        created_by=user.id,
    )

    assert workflow.status == "draft"
    assert [stage.stage_code for stage in workflow.stages] == [
        "source_upload",
        "excel_process",
        "quality_review",
        "delivery",
    ]
    assert all("cad" not in stage.stage_code and "agent" not in stage.stage_code for stage in workflow.stages)


def test_manual_workflow_stages_advance_and_finish(db):
    user, project = _owner_project(db)
    workflow = create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="File delivery", workflow_type="file_delivery"),
        created_by=user.id,
    )
    start_workflow(db, workflow)
    assert workflow.status == "waiting_input"
    assert workflow.current_stage == "source_upload"

    complete_manual_stage(workflow, "source_upload")
    assert workflow.current_stage == "quality_review"
    assert workflow.status == "waiting_input"

    complete_manual_stage(workflow, "quality_review")
    complete_manual_stage(workflow, "delivery")
    assert workflow.status == "succeeded"
    assert workflow.progress == 100
    assert workflow.finished_at is not None


def test_cancel_workflow_cancels_open_stages(db):
    user, project = _owner_project(db)
    workflow = create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Cancelled", workflow_type="file_delivery"),
        created_by=user.id,
    )
    start_workflow(db, workflow)
    cancel_workflow(workflow)

    assert workflow.status == "cancelled"
    assert all(stage.status == "cancelled" for stage in workflow.stages)
