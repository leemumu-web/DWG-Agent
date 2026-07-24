"""Authoritative workflow templates and stage capability contracts."""

from __future__ import annotations

from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import WorkflowStageCapability, WorkflowTemplateRead
from app.platform.http.exceptions import AppHTTPException


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
                artifact_types=(
                    "classified_dxf",
                    "classification_report",
                    "classification_manifest",
                ),
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
                "处理冻结的原始 Tekla Excel，生成整理表和 part。",
                execution_mode="automated",
                execution_kind="excel_stage1",
                required_inputs=("frozen_source_excel",),
                artifact_types=("stage1_excel",),
            ),
            _stage(
                "design_barrier",
                "深化设计完整性屏障",
                "人工确认图纸与基础 Excel 已具备最终合并条件。",
                artifact_types=("review_record",),
            ),
            _stage(
                "cam_packaging",
                "CAM 工作包生成",
                "预留生产规则分组、清单冻结和工作包生成契约。",
                execution_mode="placeholder",
                implementation_status="placeholder",
                execution_kind="cam_packaging",
                required_inputs=("stage1_excel", "processed_drawings"),
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


def get_stage_capability(workflow: WorkflowRun, stage_code: str) -> WorkflowStageCapability:
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
