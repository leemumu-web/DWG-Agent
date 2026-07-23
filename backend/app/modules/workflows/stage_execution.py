"""Pre-commit planning for implemented and placeholder workflow stages."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job, JobCreate, create_or_reuse_job, retry_job
from app.modules.workflows.job_sync import bind_stage_job
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import WorkflowStageExecutionCreate
from app.modules.workflows.templates import require_stage_execution
from app.platform.config.constants import (
    EXCEL_FILE_EXTENSIONS,
    TASK_DXF_TO_EXCEL,
    TASK_EXCEL_FINAL,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException, not_found, service_unavailable


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

    if payload.execution_kind == "dxf_to_excel":
        task_type, params = _prepare_dxf_to_excel(db, payload, current_user)
    elif payload.execution_kind == "excel_final":
        task_type, params = _prepare_excel_final(db, payload, current_user)
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


def _prepare_dxf_to_excel(
    db: Session,
    payload: WorkflowStageExecutionCreate,
    current_user: User,
) -> tuple[str, dict[str, object]]:
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
    return TASK_DXF_TO_EXCEL, {"batch_name": payload.batch_name}


def _prepare_excel_final(
    db: Session,
    payload: WorkflowStageExecutionCreate,
    current_user: User,
) -> tuple[str, dict[str, object]]:
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
    if stored.file_ext.lower() not in EXCEL_FILE_EXTENSIONS:
        raise AppHTTPException(
            415, "NOT_EXCEL", "Only .xls, .xlsx or .xlsm files can be processed."
        )
    return TASK_EXCEL_FINAL, {"file_id": stored.id}


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
