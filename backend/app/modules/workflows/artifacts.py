"""Workflow artifact validation and idempotent attachment."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.workflows.contracts import validate_artifact_reference
from app.modules.workflows.models import WorkflowArtifact, WorkflowRun
from app.modules.workflows.templates import get_stage_capability
from app.platform.http.exceptions import AppHTTPException


def attach_artifact(
    db: Session,
    workflow: WorkflowRun,
    *,
    stage_code: str,
    artifact_type: str,
    file_id: int | None = None,
    result_id: int | None = None,
    metadata: dict | None = None,
) -> WorkflowArtifact:
    if file_id is None and result_id is None:
        raise AppHTTPException(
            422,
            "WORKFLOW_ARTIFACT_EMPTY",
            "An artifact must reference a file or result.",
        )
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    capability = get_stage_capability(workflow, stage_code)
    if capability.artifact_types and artifact_type not in capability.artifact_types:
        raise AppHTTPException(
            422,
            "WORKFLOW_ARTIFACT_TYPE_INVALID",
            "The artifact type is not declared for this workflow stage.",
            {
                "stage_code": stage_code,
                "artifact_type": artifact_type,
                "allowed_artifact_types": capability.artifact_types,
            },
        )
    validate_artifact_reference(
        db,
        workflow,
        artifact_type=artifact_type,
        file_id=file_id,
        result_id=result_id,
    )
    existing = db.scalar(
        select(WorkflowArtifact).where(
            WorkflowArtifact.workflow_run_id == workflow.id,
            WorkflowArtifact.stage_run_id == stage.id,
            WorkflowArtifact.artifact_type == artifact_type,
            WorkflowArtifact.file_id == file_id,
            WorkflowArtifact.result_id == result_id,
        )
    )
    if existing is not None:
        return existing
    artifact = WorkflowArtifact(
        workflow=workflow,
        stage=stage,
        artifact_type=artifact_type,
        file_id=file_id,
        result_id=result_id,
        metadata_json=metadata,
    )
    db.add(artifact)
    db.flush()
    return artifact
