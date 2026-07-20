from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import WorkflowArtifact, WorkflowRun, WorkflowStageRun
from app.modules.jobs.interface import AnalysisResult, Job
from app.platform.http.exceptions import AppHTTPException, not_found
from app.schemas.workflow_schema import (
    WorkflowCreate,
    WorkflowStageCapability,
    WorkflowTemplateRead,
)

WORKFLOW_TERMINAL = {"succeeded", "failed", "cancelled"}
STAGE_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}
STAGE_ACTIVE = {"queued", "running"}

def _stage(
    code: str,
    name: str,
    description: str,
    *,
    execution_mode: str = "manual",
    implementation_status: str = "implemented",
    execution_kind: str | None = None,
    required_inputs: tuple[str, ...] = (),
    artifact_types: tuple[str, ...] = (),
) -> WorkflowStageCapability:
    return WorkflowStageCapability(
        code=code,
        name=name,
        description=description,
        execution_mode=execution_mode,
        implementation_status=implementation_status,
        execution_kind=execution_kind,
        required_inputs=list(required_inputs),
        artifact_types=list(artifact_types),
    )


WORKFLOW_TEMPLATES: dict[str, WorkflowTemplateRead] = {
    "excel_delivery": WorkflowTemplateRead(
        code="excel_delivery",
        name="Excel 零件清单交付",
        description="兼容的 Excel 人工交付流程。",
        stages=[
            _stage("source_upload", "上传源 Excel", "登记并确认源 Excel。"),
            _stage("excel_process", "Excel 零件清单处理", "确认 Excel 处理结果。"),
            _stage("quality_review", "结果确认", "人工复核结果。"),
            _stage("delivery", "交付归档", "确认交付并归档。"),
        ],
    ),
    "file_delivery": WorkflowTemplateRead(
        code="file_delivery",
        name="通用文件交付",
        description="兼容的文件人工交付流程。",
        stages=[
            _stage("source_upload", "上传源文件", "登记并确认源文件。"),
            _stage("quality_review", "文件确认", "人工复核文件。"),
            _stage("delivery", "交付归档", "确认交付并归档。"),
        ],
    ),
    "linux_production": WorkflowTemplateRead(
        code="linux_production",
        name="Linux 生产流程",
        description="从输入冻结到交付归档的服务器端生产编排框架。",
        stages=[
            _stage(
                "source_intake",
                "文件接收与输入冻结",
                "登记多个 DWG 与一个 Excel，由服务器生成配对 DXF 后冻结输入。",
                required_inputs=("dwg_files", "excel_file"),
                artifact_types=("source_file", "source_excel", "derived_dxf"),
            ),
            _stage(
                "dxf_classification",
                "DXF 分类与分流",
                "调用 Steel DXF Classifier 1.1.0 预处理并按零件类型分流冻结 DXF。",
                execution_mode="automated",
                execution_kind="steel_dxf_classification",
                required_inputs=("frozen_derived_dxf",),
                artifact_types=("classified_dxf", "classification_report", "classification_manifest"),
            ),
            _stage(
                "drawing_processing",
                "图纸分类与拆板",
                "预留自动拆板、人工拆板回流与独立校验契约；分类分流已在上一阶段完成。",
                execution_mode="placeholder",
                implementation_status="placeholder",
                execution_kind="drawing_processing",
                required_inputs=("drawing_files",),
                artifact_types=("processed_drawing", "validation_report"),
            ),
            _stage(
                "excel_stage1",
                "Excel 第一阶段处理",
                "调用现有 DXF 批次提取管线生成基础工作簿。",
                execution_mode="automated",
                execution_kind="dxf_to_excel",
                required_inputs=("batch_name",),
                artifact_types=("stage1_excel",),
            ),
            _stage(
                "design_barrier",
                "深化设计完整性屏障",
                "人工确认图纸与基础 Excel 已具备最终合并条件。",
                artifact_types=("review_record",),
            ),
            _stage(
                "excel_final",
                "Excel 最终合并",
                "调用现有 Excel Final 管线并导入结构化零件数据。",
                execution_mode="automated",
                execution_kind="excel_final",
                required_inputs=("file_id",),
                artifact_types=("final_excel",),
            ),
            _stage(
                "cam_packaging",
                "CAM 工作包生成",
                "预留生产规则分组、清单冻结和工作包生成契约。",
                execution_mode="placeholder",
                implementation_status="placeholder",
                execution_kind="cam_packaging",
                required_inputs=("final_excel", "processed_drawings"),
                artifact_types=("cam_package",),
            ),
            _stage(
                "windows_cam",
                "Windows CAM 排版",
                "预留 Node Agent、租约、fencing token 与 SinoCAM 执行契约。",
                execution_mode="external",
                implementation_status="external",
                execution_kind="windows_cam",
                required_inputs=("cam_package",),
                artifact_types=("cam_result", "runner_diagnostics"),
            ),
            _stage(
                "result_acceptance",
                "CAM 结果接纳",
                "预留结果清单、摘要校验和正式接纳契约。",
                execution_mode="placeholder",
                implementation_status="placeholder",
                execution_kind="result_acceptance",
                required_inputs=("cam_result",),
                artifact_types=("acceptance_report",),
            ),
            _stage(
                "delivery_archive",
                "交付与归档",
                "确认正式产物可下载并完成生产流程。",
                artifact_types=("delivery_file",),
            ),
        ],
    ),
}

WORKFLOW_DEFINITIONS: dict[str, tuple[tuple[str, str], ...]] = {
    code: tuple((stage.code, stage.name) for stage in template.stages)
    for code, template in WORKFLOW_TEMPLATES.items()
}


def list_workflow_templates() -> list[WorkflowTemplateRead]:
    return list(WORKFLOW_TEMPLATES.values())


def get_stage_capability(
    workflow: WorkflowRun, stage_code: str
) -> WorkflowStageCapability:
    template = WORKFLOW_TEMPLATES[workflow.workflow_type]
    capability = next((stage for stage in template.stages if stage.code == stage_code), None)
    if capability is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    return capability


def require_stage_execution(
    workflow: WorkflowRun,
    *,
    stage_code: str,
    execution_kind: str,
) -> WorkflowStageCapability:
    capability = get_stage_capability(workflow, stage_code)
    if workflow.current_stage != stage_code:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_NOT_CURRENT",
            "Only the current workflow stage can be executed.",
        )
    if capability.execution_kind != execution_kind:
        raise AppHTTPException(
            422,
            "WORKFLOW_EXECUTION_KIND_INVALID",
            "The execution kind does not match this workflow stage.",
        )
    return capability


def create_workflow(db: Session, payload: WorkflowCreate, *, created_by: int) -> WorkflowRun:
    workflow = WorkflowRun(
        project_id=payload.project_id,
        created_by=created_by,
        name=payload.name,
        workflow_type=payload.workflow_type,
        status="draft",
        progress=0,
        config_json=payload.config,
    )
    db.add(workflow)
    db.flush()
    for sequence, (stage_code, name) in enumerate(
        WORKFLOW_DEFINITIONS[payload.workflow_type], start=1
    ):
        db.add(
            WorkflowStageRun(
                workflow_run_id=workflow.id,
                stage_code=stage_code,
                name=name,
                sequence=sequence,
                status="ready" if sequence == 1 else "pending",
                progress=0,
            )
        )
    db.flush()
    return workflow


def get_workflow_or_404(db: Session, workflow_id: int) -> WorkflowRun:
    workflow = db.scalar(select(WorkflowRun).where(WorkflowRun.id == workflow_id))
    if workflow is None:
        raise not_found("Workflow")
    return workflow


def start_workflow(db: Session, workflow: WorkflowRun) -> WorkflowRun:
    if workflow.status != "draft":
        raise AppHTTPException(409, "WORKFLOW_NOT_DRAFT", "Only a draft workflow can start.")
    first = min(workflow.stages, key=lambda stage: stage.sequence)
    now = datetime.now(UTC)
    workflow.status = "waiting_input"
    workflow.current_stage = first.stage_code
    workflow.started_at = now
    first.status = "waiting_input"
    first.started_at = now
    return workflow


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
            422, "WORKFLOW_ARTIFACT_EMPTY", "An artifact must reference a file or result."
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


def bind_stage_job(db: Session, workflow: WorkflowRun, *, stage_code: str, job: Job) -> None:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if workflow.status in WORKFLOW_TERMINAL:
        if workflow.status != "failed" or workflow.current_stage != stage_code:
            raise AppHTTPException(
                409, "WORKFLOW_TERMINAL", "Terminal workflow cannot accept a job."
            )
    stage.job_id = job.id
    stage.job_attempt = job.attempt
    stage.status = job.status
    stage.progress = job.progress
    stage.error_code = None
    stage.error_message = None
    stage.finished_at = None
    stage.started_at = job.started_at or datetime.now(UTC)
    workflow.current_stage = stage.stage_code
    workflow.status = "running"
    workflow.error_code = None
    workflow.error_message = None
    workflow.finished_at = None
    recompute_workflow(workflow)
    db.flush()


def sync_workflow_from_jobs(db: Session, workflow: WorkflowRun) -> WorkflowRun:
    now = datetime.now(UTC)
    for stage in workflow.stages:
        if stage.job_id is None:
            continue
        job = db.get(Job, stage.job_id)
        if job is None or job.attempt != stage.job_attempt:
            continue
        stage.status = job.status
        stage.progress = job.progress
        stage.error_code = job.error_code
        stage.error_message = job.error_message
        stage.started_at = job.started_at or stage.started_at
        stage.finished_at = job.finished_at
        if job.status == "succeeded":
            capability = get_stage_capability(workflow, stage.stage_code)
            results = list(
                db.scalars(
                    select(AnalysisResult).where(
                        AnalysisResult.job_id == job.id,
                        AnalysisResult.status == "succeeded",
                    )
                ).all()
            )
            for result in results:
                requested_artifact_type = (
                    result.result_json.get("workflow_artifact_type")
                    if isinstance(result.result_json, dict)
                    else None
                )
                artifact_type = (
                    requested_artifact_type
                    if isinstance(requested_artifact_type, str)
                    and requested_artifact_type in capability.artifact_types
                    else capability.artifact_types[0]
                    if capability.artifact_types
                    else f"{stage.stage_code}_result"
                )
                attach_artifact(
                    db,
                    workflow,
                    stage_code=stage.stage_code,
                    artifact_type=artifact_type,
                    file_id=result.result_file_id,
                    result_id=result.id,
                    metadata={"job_id": job.id, "job_attempt": job.attempt},
                )
            next_stage = _next_stage(workflow, stage.sequence)
            if next_stage is not None and next_stage.status == "pending":
                next_stage.status = (
                    "waiting_review" if stage.stage_code == "excel_process" else "waiting_input"
                )
                next_stage.started_at = now
    recompute_workflow(workflow)
    db.flush()
    return workflow


def complete_manual_stage(workflow: WorkflowRun, stage_code: str) -> WorkflowRun:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if stage.status not in {"ready", "waiting_input", "waiting_review"}:
        raise AppHTTPException(
            409, "WORKFLOW_STAGE_NOT_ACTIONABLE", "This workflow stage is not awaiting input."
        )
    capability = get_stage_capability(workflow, stage_code)
    if capability.execution_mode == "automated":
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_REQUIRES_EXECUTION",
            "This automated stage must use its execution endpoint.",
        )
    if (
        workflow.workflow_type == "linux_production"
        and stage_code == "source_intake"
        and (
            workflow.input_batch is None
            or workflow.input_batch.status != "frozen"
        )
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_INPUT_BATCH_NOT_FROZEN",
            "The production input batch must be validated and frozen through its dedicated endpoint.",
        )
    if (
        capability.execution_mode in {"placeholder", "external"}
        and not stage.artifacts
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_HANDOFF_ARTIFACT_REQUIRED",
            "At least one handoff artifact must be bound before confirming this stage.",
        )
    now = datetime.now(UTC)
    stage.status = "succeeded"
    stage.progress = 100
    stage.finished_at = now
    next_stage = _next_stage(workflow, stage.sequence)
    if next_stage is not None and next_stage.status == "pending":
        next_stage.status = "waiting_input"
        next_stage.started_at = now
    recompute_workflow(workflow)
    return workflow


def cancel_workflow(workflow: WorkflowRun) -> WorkflowRun:
    if workflow.status in WORKFLOW_TERMINAL:
        raise AppHTTPException(409, "WORKFLOW_TERMINAL", "Workflow is already terminal.")
    now = datetime.now(UTC)
    workflow.status = "cancelled"
    workflow.finished_at = now
    for stage in workflow.stages:
        if stage.status not in STAGE_TERMINAL:
            stage.status = "cancelled"
            stage.finished_at = now
    return workflow


def recompute_workflow(workflow: WorkflowRun) -> None:
    stages = sorted(workflow.stages, key=lambda item: item.sequence)
    if not stages:
        workflow.progress = 0
        return
    workflow.progress = round(sum(stage.progress for stage in stages) / len(stages))
    if workflow.status == "cancelled":
        return
    failed = next((stage for stage in stages if stage.status == "failed"), None)
    if failed is not None:
        workflow.status = "failed"
        workflow.current_stage = failed.stage_code
        workflow.error_code = failed.error_code
        workflow.error_message = failed.error_message
        workflow.finished_at = failed.finished_at or datetime.now(UTC)
        return
    cancelled = next((stage for stage in stages if stage.status == "cancelled"), None)
    if cancelled is not None:
        workflow.status = "failed"
        workflow.current_stage = cancelled.stage_code
        workflow.error_code = "WORKFLOW_STAGE_CANCELLED"
        workflow.error_message = "The current stage job was cancelled and can be retried."
        workflow.finished_at = cancelled.finished_at or datetime.now(UTC)
        return
    if all(stage.status in {"succeeded", "skipped"} for stage in stages):
        workflow.status = "succeeded"
        workflow.progress = 100
        workflow.current_stage = stages[-1].stage_code
        workflow.finished_at = datetime.now(UTC)
        return
    current = next((stage for stage in stages if stage.status not in STAGE_TERMINAL), stages[-1])
    workflow.current_stage = current.stage_code
    if current.status in STAGE_ACTIVE:
        workflow.status = "running"
    elif current.status == "waiting_review":
        workflow.status = "waiting_review"
    elif current.status in {"ready", "waiting_input"}:
        workflow.status = "waiting_input"


def _next_stage(workflow: WorkflowRun, sequence: int) -> WorkflowStageRun | None:
    return next((stage for stage in workflow.stages if stage.sequence == sequence + 1), None)
