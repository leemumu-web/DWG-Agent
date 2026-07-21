"""Workflow artifact attachment endpoint."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    require_job_read_access,
)
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_role
from app.modules.workflows.access import WORKFLOW_WRITE_ROLES, load_workflow_detail
from app.modules.workflows.artifacts import attach_artifact
from app.modules.workflows.schemas import (
    WorkflowArtifactCreate,
    WorkflowArtifactRead,
    WorkflowDetail,
)
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import not_found

router = APIRouter()


@router.post(
    "/{workflow_id}/artifacts",
    status_code=status.HTTP_201_CREATED,
    summary="绑定工作流文件或结果产物",
    description=(
        "复用文件中心和分析结果中的既有登记，只保存引用，不重复上传字节。"
        "同一阶段、类型和引用的重复请求幂等返回已有产物。"
    ),
)
def create_workflow_artifact(
    workflow_id: int,
    payload: WorkflowArtifactCreate,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    if payload.file_id is not None:
        stored = db.get(StoredFile, payload.file_id)
        if stored is None or stored.status == "deleted":
            raise not_found("File")
        require_file_read_access(db, current_user, stored)
    if payload.result_id is not None:
        result = db.get(AnalysisResult, payload.result_id)
        if result is None:
            raise not_found("Result")
        job = db.get(Job, result.job_id)
        if job is None:
            raise not_found("Job")
        require_job_read_access(db, current_user, job)
    known_artifact_ids = {artifact.id for artifact in workflow.artifacts}
    artifact = attach_artifact(
        db,
        workflow,
        stage_code=payload.stage_code,
        artifact_type=payload.artifact_type,
        file_id=payload.file_id,
        result_id=payload.result_id,
        metadata=payload.metadata,
    )
    reused = artifact.id in known_artifact_ids
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=("workflow_artifacts.reuse" if reused else "workflow_artifacts.create"),
        resource_type="workflow",
        resource_id=workflow.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    if reused:
        response.status_code = status.HTTP_200_OK
    return ok(
        {
            "artifact": WorkflowArtifactRead.model_validate(artifact),
            "workflow": WorkflowDetail.model_validate(load_workflow_detail(db, workflow.id)),
            "reused": reused,
        },
        request.state.request_id,
    )
