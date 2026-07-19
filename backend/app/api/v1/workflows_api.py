from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    CurrentUser,
    get_db,
    has_global_project_access,
    require_project_member,
    require_project_role,
)
from app.core.config import settings
from app.core.constants import TASK_DXF_TO_EXCEL, TASK_EXCEL_FINAL
from app.core.exceptions import AppHTTPException, not_found, service_unavailable
from app.db.pagination import paginate_scalars
from app.models.file import StoredFile
from app.models.job import Job
from app.models.project import ProjectMember
from app.models.result import AnalysisResult
from app.models.workflow import WorkflowRun
from app.schemas.common import ok
from app.schemas.common import page as page_response
from app.schemas.job_schema import JobCreate, JobRead
from app.schemas.workflow_schema import (
    WorkflowArtifactCreate,
    WorkflowArtifactRead,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowRead,
    WorkflowStageExecutionCreate,
)
from app.services.audit_service import write_audit_log
from app.services.file_service import require_file_read_access
from app.services.job_access import require_job_read_access
from app.services.job_service import cancel_job as transition_job_to_cancelled
from app.services.job_service import create_or_reuse_job, dispatch_committed_job
from app.services.workflow_service import (
    attach_artifact,
    bind_stage_job,
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    get_workflow_or_404,
    list_workflow_templates,
    require_stage_execution,
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


@router.get(
    "/templates",
    summary="列出工作流模板与阶段能力",
    description="返回后端权威的阶段顺序、执行方式、实现状态和输入输出契约。",
)
def get_workflow_templates(request: Request, current_user: CurrentUser):
    return ok(list_workflow_templates(), request.state.request_id)


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
    workflow = _load_detail(db, workflow_id)
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
        action="workflow_artifacts.reuse" if reused else "workflow_artifacts.create",
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
            "workflow": WorkflowDetail.model_validate(_load_detail(db, workflow.id)),
            "reused": reused,
        },
        request.state.request_id,
    )


@router.post(
    "/{workflow_id}/stages/{stage_code}/executions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="执行工作流自动或外部阶段",
    description=(
        "按后端模板能力调用已实现的 Linux Job；未实现阶段保留同一路径并返回稳定能力边界。"
    ),
)
def execute_workflow_stage(
    workflow_id: int,
    stage_code: str,
    payload: WorkflowStageExecutionCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = _load_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    capability = require_stage_execution(
        workflow,
        stage_code=stage_code,
        execution_kind=payload.execution_kind,
    )
    if capability.implementation_status != "implemented":
        raise AppHTTPException(
            501,
            "WORKFLOW_STAGE_NOT_IMPLEMENTED",
            "This workflow stage has an API contract but no server implementation yet.",
            {
                "stage_code": stage_code,
                "execution_kind": payload.execution_kind,
                "implementation_status": capability.implementation_status,
                "execution_mode": capability.execution_mode,
                "required_inputs": capability.required_inputs,
                "artifact_types": capability.artifact_types,
            },
        )
    if payload.execution_kind == "dxf_to_excel":
        if not settings.dxf2excel_pipeline_enabled:
            raise service_unavailable(
                "DXF2EXCEL_PIPELINE_DISABLED",
                "DXF→Excel pipeline is disabled. Set DXF2EXCEL_PIPELINE_ENABLED=true to enable.",
            )
        if payload.batch_name is None:
            raise AppHTTPException(
                422,
                "WORKFLOW_BATCH_REQUIRED",
                "batch_name is required for the DXF-to-Excel workflow stage.",
            )
        batch_files = list(
            db.scalars(
                select(StoredFile).where(
                    StoredFile.batch_name == payload.batch_name,
                    StoredFile.file_ext == ".dxf",
                    StoredFile.status != "deleted",
                )
            ).all()
        )
        if not batch_files:
            raise not_found("DXF batch")
        for stored in batch_files:
            require_file_read_access(db, current_user, stored)
        task_type = TASK_DXF_TO_EXCEL
        params = {"batch_name": payload.batch_name}
    elif payload.execution_kind == "excel_final":
        if not settings.excel_final_pipeline_enabled:
            raise service_unavailable(
                "EXCEL_FINAL_PIPELINE_DISABLED",
                "Excel→Final pipeline is disabled. Set EXCEL_FINAL_PIPELINE_ENABLED=true to enable.",
            )
        if payload.file_id is None:
            raise AppHTTPException(
                422,
                "WORKFLOW_EXCEL_FILE_REQUIRED",
                "file_id is required for the Excel Final workflow stage.",
            )
        stored = db.get(StoredFile, payload.file_id)
        if stored is None or stored.status == "deleted":
            raise not_found("File")
        require_file_read_access(db, current_user, stored)
        if stored.file_ext.lower() not in {".xls", ".xlsx"}:
            raise AppHTTPException(
                415,
                "NOT_EXCEL",
                "Only .xls or .xlsx files can be processed.",
            )
        task_type = TASK_EXCEL_FINAL
        params = {"file_id": stored.id}
    else:
        raise AppHTTPException(
            501,
            "WORKFLOW_STAGE_NOT_IMPLEMENTED",
            "This workflow stage has an API contract but no server implementation yet.",
            {"stage_code": stage_code, "execution_kind": payload.execution_kind},
        )
    job, reused = create_or_reuse_job(
        db,
        JobCreate(
            project_id=workflow.project_id,
            task_type=task_type,
            params=params,
        ),
        created_by=current_user.id,
        request_key=f"workflow-{workflow.id}-{stage_code}",
    )
    bind_stage_job(db, workflow, stage_code=stage_code, job=job)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_stages.execution_reused" if reused else "workflow_stages.execute",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "stage_code": stage_code,
            "execution_kind": payload.execution_kind,
            "job_id": job.id,
        },
        request=request,
    )
    db.commit()
    if not reused:
        dispatch_committed_job(db, job)
    return ok(
        {
            "workflow": WorkflowDetail.model_validate(_load_detail(db, workflow.id)),
            "job": JobRead.model_validate(job),
            "reused": reused,
        },
        request.state.request_id,
    )


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
