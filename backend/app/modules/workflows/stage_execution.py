"""Pre-commit planning for implemented and placeholder workflow stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    latest_classification_run,
    list_split_candidate_inputs,
)
from app.modules.dxf_splitting.interface import (
    MAX_AUTOMATIC_ATTEMPTS,
    get_dxf_split_outcome,
    get_excel_split_handoff,
)
from app.modules.excel_processing.interface import ExcelFinalInputError
from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import User
from app.modules.jobs.interface import (
    Job,
    JobCreate,
    create_or_reuse_job,
    rerun_succeeded_job,
    retry_job,
)
from app.modules.workflows.contracts import require_stage_inputs
from app.modules.workflows.intake import registration
from app.modules.workflows.job_sync import bind_stage_job
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import WorkflowStageExecutionCreate
from app.modules.workflows.templates import require_stage_execution
from app.platform.config.constants import (
    TASK_EXCEL_FINAL,
    TASK_STEEL_DXF_CLASSIFICATION,
    TASK_STEEL_DXF_SPLIT,
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


def dispatch_stage_execution(
    db: Session,
    workflow: WorkflowRun,
    plan: StageExecutionPlan,
    *,
    dispatcher: Callable[[Session, Job], object],
) -> StageExecutionPlan:
    """Dispatch a committed stage, including the split pipeline's retry budget."""
    current = plan
    while current.should_dispatch:
        try:
            dispatcher(db, current.job)
            return current
        except AppHTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            job = db.get(Job, current.job.id, populate_existing=True)
            if (
                current.job.task_type != TASK_STEEL_DXF_SPLIT
                or code != "JOB_ENQUEUE_FAILED"
                or job is None
                or job.status != "failed"
                or job.attempt >= MAX_AUTOMATIC_ATTEMPTS
            ):
                raise
            job = retry_job(db, job)
            bind_stage_job(
                db,
                workflow,
                stage_code="drawing_processing",
                job=job,
            )
            db.commit()
            current = StageExecutionPlan(
                job=job,
                reused=current.reused,
                retried=True,
            )
    return current


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
    require_stage_inputs(workflow, stage_code)

    if payload.execution_kind == "excel_stage1":
        task_type, params = _prepare_excel_stage1(db, workflow, current_user)
    elif payload.execution_kind == "steel_dxf_classification":
        task_type, params = _prepare_dxf_classification(db, workflow, current_user)
    elif payload.execution_kind == "drawing_processing":
        task_type, params = _prepare_dxf_splitting(db, workflow, current_user)
    else:
        raise AppHTTPException(
            501,
            "WORKFLOW_STAGE_NOT_IMPLEMENTED",
            "This workflow stage has an API contract but no server implementation yet.",
            {"stage_code": stage_code, "execution_kind": payload.execution_kind},
        )

    job = (
        _bound_dxf_split_job(
            db,
            workflow,
            stage_code=stage_code,
            task_type=task_type,
            params=params,
        )
        if stage_code == "drawing_processing"
        else None
    )
    reused = job is not None
    if job is None:
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
        if stage_code == "drawing_processing" and job.attempt >= MAX_AUTOMATIC_ATTEMPTS:
            raise AppHTTPException(
                409,
                "DXF_SPLIT_ATTEMPTS_EXHAUSTED",
                "拆板任务已用完三次完整批次尝试，不能继续重跑。",
                {"job_id": job.id, "attempt": job.attempt},
            )
        job = retry_job(db, job)
    elif reused and job.status == "succeeded" and stage_code == "drawing_processing":
        outcome = get_dxf_split_outcome(
            db,
            job_id=job.id,
            attempt=job.attempt,
        )
        if outcome == "completed_with_review":
            if job.attempt >= MAX_AUTOMATIC_ATTEMPTS:
                raise AppHTTPException(
                    409,
                    "DXF_SPLIT_ATTEMPTS_EXHAUSTED",
                    "拆板任务已用完三次完整批次尝试，不能继续重跑。",
                    {"job_id": job.id, "attempt": job.attempt},
                )
            job = rerun_succeeded_job(db, job)
            retried = True
    bind_stage_job(db, workflow, stage_code=stage_code, job=job)
    return StageExecutionPlan(job=job, reused=reused, retried=retried)


def _bound_dxf_split_job(
    db: Session,
    workflow: WorkflowRun,
    *,
    stage_code: str,
    task_type: str,
    params: dict[str, object],
) -> Job | None:
    stage = next(
        (item for item in workflow.stages if item.stage_code == stage_code),
        None,
    )
    if stage is None or stage.job_id is None:
        return None
    job = db.get(Job, stage.job_id)
    if (
        job is None
        or job.project_id != workflow.project_id
        or job.task_type != task_type
        or job.params_json != params
        or job.attempt != stage.job_attempt
    ):
        raise AppHTTPException(
            409,
            "DXF_SPLIT_JOB_BINDING_INVALID",
            "当前拆板阶段绑定的 Job 与工作流冻结输入不一致。",
            {
                "workflow_id": workflow.id,
                "stage_job_id": stage.job_id,
                "stage_job_attempt": stage.job_attempt,
            },
        )
    return job


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
        (stage for stage in workflow.stages if stage.stage_code == "source_intake"),
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
                "artifact_file_ids": [artifact.file_id for artifact in source_artifacts],
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
    split_handoff = get_excel_split_handoff(db, workflow.id)
    # The split handoff already proves current workflow/run/attempt lineage and
    # file availability. Its server-generated files may have another member as
    # uploader, so project-role authorization is the applicable boundary.
    return TASK_EXCEL_FINAL, {
        "file_id": stored.id,
        "workflow_id": workflow.id,
        "input_manifest_sha256": batch.manifest_sha256,
        "dxf_split_handoff": split_handoff.model_dump(mode="json"),
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


def _prepare_dxf_splitting(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
) -> tuple[str, dict[str, object]]:
    if not settings.dxf_split_pipeline_enabled:
        raise service_unavailable(
            "DXF_SPLIT_PIPELINE_DISABLED",
            "DXF 拆板服务当前未启用；部署和人工验证完成后再打开开关。",
        )
    classification = latest_classification_run(db, workflow.id)
    if classification is None:
        raise AppHTTPException(
            409,
            "DXF_CLASSIFICATION_RUN_REQUIRED",
            "拆板前必须存在已完成的 DXF 分类运行。",
        )
    classification_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == "dxf_classification"),
        None,
    )
    if (
        classification_stage is None
        or classification_stage.job_id != classification.job_id
        or classification_stage.job_attempt != classification.job_attempt
    ):
        raise AppHTTPException(
            409,
            "DXF_CLASSIFICATION_RUN_STALE",
            "最新分类运行不是当前工作流登记的正式分类尝试。",
        )
    classification_job = db.get(Job, classification.job_id)
    if (
        classification.project_id != workflow.project_id
        or classification_job is None
        or classification_job.project_id != workflow.project_id
        or classification_job.task_type != TASK_STEEL_DXF_CLASSIFICATION
        or classification_job.status != "succeeded"
        or classification_job.attempt != classification.job_attempt
    ):
        raise AppHTTPException(
            409,
            "DXF_CLASSIFICATION_PROJECT_MISMATCH",
            "当前分类运行没有绑定本项目已成功的正式 Job attempt。",
            {"classification_run_id": classification.id},
        )
    if classification.status not in {"completed", "completed_with_review"}:
        raise AppHTTPException(
            409,
            "DXF_CLASSIFICATION_NOT_READY",
            "分类阶段尚未形成可追溯的 DXF 输出。",
            {
                "classification_run_id": classification.id,
                "review_required_count": classification.review_required_count,
                "unreadable_count": classification.unreadable_count,
            },
        )
    inputs = list_split_candidate_inputs(db, workflow.id)
    if not inputs:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_INPUT_REQUIRED",
            "分类运行没有可供拆板的 DXF。",
        )
    for item in inputs:
        stored = db.get(StoredFile, item.output_file_id)
        if (
            stored is None
            or stored.status == "deleted"
            or stored.file_ext.casefold() != ".dxf"
        ):
            raise AppHTTPException(
                409,
                "DXF_SPLIT_SOURCE_MISSING",
                "分类后的拆板输入已不可用。",
                {
                    "classification_item_id": item.classification_item_id,
                    "file_id": item.output_file_id,
                },
            )
    # The route already authorizes the caller's project role. Classified DXFs
    # are server-generated workflow artifacts and may have been created by a
    # different project member, so uploader identity is not the read boundary.
    return TASK_STEEL_DXF_SPLIT, {
        "workflow_id": workflow.id,
        "classification_run_id": classification.id,
        "input_manifest_sha256": classification.input_manifest_sha256,
    }
