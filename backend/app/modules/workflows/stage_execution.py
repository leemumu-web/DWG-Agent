"""Pre-commit planning for implemented and placeholder workflow stages."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.excel_processing.interface import ExcelFinalInputError
from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job, JobCreate, create_or_reuse_job, retry_job
from app.modules.workflows.intake import registration
from app.modules.workflows.job_sync import bind_stage_job
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import WorkflowStageExecutionCreate
from app.modules.workflows.templates import require_stage_execution
from app.platform.config.constants import (
    TASK_EXCEL_FINAL,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException, service_unavailable


@dataclass(frozen=True)
class StageExecutionPlan:
    job: Job
    reused: bool
    retried: bool

    @property
    def should_dispatch(self) -> bool:
        return not self.reused or self.retried


def prepare_stage_execution(
    db: Session,
    workflow: WorkflowRun,
    *,
    stage_code: str,
    payload: WorkflowStageExecutionCreate,
    current_user: User,
) -> StageExecutionPlan:
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

    if payload.execution_kind == "excel_stage1":
        task_type, params = _prepare_excel_stage1(db, workflow, current_user)
    elif payload.execution_kind == "steel_dxf_classification":
        task_type, params = _prepare_dxf_classification(db, workflow, current_user)
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
    retried = reused and job.status in {"failed", "cancelled"}
    if retried:
        job = retry_job(db, job)
    bind_stage_job(db, workflow, stage_code=stage_code, job=job)
    return StageExecutionPlan(job=job, reused=reused, retried=retried)


def _prepare_excel_stage1(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
) -> tuple[str, dict[str, object]]:
    if not settings.excel_final_pipeline_enabled:
        raise service_unavailable(
            "EXCEL_STAGE1_PIPELINE_DISABLED",
            "Excel 第一阶段处理服务当前未启用。",
        )
    batch = workflow.input_batch
    if batch is None or batch.status != "frozen":
        raise AppHTTPException(
            409,
            "WORKFLOW_INPUT_BATCH_NOT_FROZEN",
            "生产输入批次必须先完成校验和冻结。",
        )
    if not batch.manifest_sha256:
        raise AppHTTPException(
            409,
            "WORKFLOW_INPUT_MANIFEST_MISSING",
            "冻结输入批次缺少不可变清单摘要。",
        )
    excel_items = [item for item in batch.items if item.role == "source_excel"]
    if len(excel_items) != 1 or excel_items[0].status != "frozen":
        raise AppHTTPException(
            409,
            "WORKFLOW_SOURCE_EXCEL_COUNT_INVALID",
            "冻结输入批次必须包含且只能包含一个有效 Excel。",
            {"excel_count": len(excel_items)},
        )
    item = excel_items[0]
    source_intake_stage = next(
        (
            stage
            for stage in workflow.stages
            if stage.stage_code == "source_intake"
        ),
        None,
    )
    source_artifacts = [
        artifact
        for artifact in workflow.artifacts
        if artifact.artifact_type == "source_excel"
        and source_intake_stage is not None
        and artifact.stage_run_id == source_intake_stage.id
    ]
    if (
        len(source_artifacts) != 1
        or source_artifacts[0].file_id != item.file_id
        or source_artifacts[0].result_id is not None
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_SOURCE_EXCEL_ARTIFACT_INVALID",
            "冻结输入清单与 source_excel 产物不一致。",
            {
                "item_file_id": item.file_id,
                "artifact_file_ids": [
                    artifact.file_id for artifact in source_artifacts
                ],
            },
        )
    stored = db.get(StoredFile, item.file_id)
    if stored is None or stored.status == "deleted":
        raise AppHTTPException(
            409,
            "WORKFLOW_SOURCE_EXCEL_FILE_MISSING",
            "冻结的 Excel 源文件已不可用。",
            {"file_id": item.file_id},
        )
    require_file_read_access(db, current_user, stored)
    if (
        item.validated_sha256 is None
        or item.validation_contract_version is None
        or not isinstance(item.validation_json, dict)
        or not isinstance(item.validation_json.get("inspection"), dict)
    ):
        registration.raise_excel_failure(
            registration.excel_validation_required_failure(
                item,
                message="冻结的 Excel 输入缺少可核验的登记记录。",
                action="请返回输入阶段，移除该 Excel 后重新上传并冻结。",
            )
        )
    payload = registration.read_verified_input_object(stored)
    try:
        inspection = registration.inspect_excel_payload(
            file_name=stored.original_name,
            payload=payload,
            expected_sha256=item.validated_sha256,
        )
    except ExcelFinalInputError as exc:
        registration.raise_excel_failure(exc.failure.as_dict())
    if inspection.input_contract_version != item.validation_contract_version:
        registration.raise_excel_failure(
            registration.excel_validation_required_failure(
                item,
                message="冻结的 Excel 输入规则版本已失效。",
                action="请返回输入阶段，重新登记并冻结 Excel。",
            )
        )
    return TASK_EXCEL_FINAL, {
        "file_id": stored.id,
        "workflow_id": workflow.id,
        "input_manifest_sha256": batch.manifest_sha256,
    }


def _prepare_dxf_classification(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
) -> tuple[str, dict[str, object]]:
    if not settings.dxf_classification_pipeline_enabled:
        raise service_unavailable(
            "DXF_CLASSIFICATION_PIPELINE_DISABLED",
            "DXF classification pipeline is disabled. Set DXF_CLASSIFICATION_PIPELINE_ENABLED=true to enable.",
        )
    batch = workflow.input_batch
    if batch is None or batch.status != "frozen" or not batch.manifest_sha256:
        raise AppHTTPException(
            409,
            "WORKFLOW_INPUT_BATCH_NOT_FROZEN",
            "The production input batch must be frozen before DXF classification.",
        )
    derived_files: list[StoredFile] = []
    for item in batch.items:
        if item.role != "source_dwg" or item.derived_dxf_file_id is None:
            continue
        derived = db.get(StoredFile, item.derived_dxf_file_id)
        if derived is None or derived.status == "deleted":
            raise AppHTTPException(
                409,
                "CLASSIFICATION_SOURCE_MISSING",
                "A frozen derived DXF is unavailable.",
                {"item_id": item.id, "file_id": item.derived_dxf_file_id},
            )
        require_file_read_access(db, current_user, derived)
        derived_files.append(derived)
    if not derived_files:
        raise AppHTTPException(
            409,
            "CLASSIFICATION_SOURCE_REQUIRED",
            "The frozen input batch contains no derived DXF files.",
        )
    return TASK_STEEL_DXF_CLASSIFICATION, {
        "workflow_id": workflow.id,
        "input_manifest_sha256": batch.manifest_sha256,
    }
