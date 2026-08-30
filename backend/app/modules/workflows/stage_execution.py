"""Pre-commit planning for implemented and placeholder workflow stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    ClassificationError,
    latest_classification_run,
    list_pl_split_candidate_inputs,
    list_split_candidate_inputs,
    list_xbox_split_candidate_inputs,
    load_bh_stage2_classification_batch,
    load_box_stage2_classification_batch,
)
from app.modules.dxf_splitting.interface import (
    MAX_AUTOMATIC_ATTEMPTS,
    get_excel_split_handoff,
)
from app.modules.excel_processing.interface import ExcelFinalInputError
from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import User
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    JobCreate,
    create_or_reuse_job,
    retry_job,
)
from app.modules.workflows.contracts import require_stage_inputs
from app.modules.workflows.intake import registration
from app.modules.workflows.job_sync import bind_stage_job
from app.modules.workflows.models import WorkflowInputBatch, WorkflowInputItem, WorkflowRun
from app.modules.workflows.schemas import WorkflowStageExecutionCreate
from app.modules.workflows.templates import require_stage_execution
from app.platform.config.constants import (
    TASK_EXCEL_FINAL,
    TASK_EXCEL_STAGE2,
    TASK_EXCEL_STAGE3,
    TASK_PL_DXF_SPLIT,
    TASK_STEEL_DXF_CLASSIFICATION,
    TASK_STEEL_DXF_SPLIT,
)
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException, service_unavailable
from app.platform.storage.base import StorageError, StorageObjectNotFound


@dataclass(frozen=True)
class StageExecutionPlan:
    job: Job
    reused: bool
    retried: bool

    @property
    def should_dispatch(self) -> bool:
        return not self.reused or self.retried


def preflight_excel_stage1(
    db: Session,
    workflow: WorkflowRun,
    *,
    current_user: User,
) -> dict[str, object]:
    """Run the exact Excel execution gate without creating or binding a Job."""
    require_stage_execution(
        workflow,
        stage_code="excel_stage1",
        execution_kind="excel_stage1",
    )
    require_stage_inputs(workflow, "excel_stage1")
    _, params = _prepare_excel_stage1(db, workflow, current_user)
    source = db.get(StoredFile, int(params["file_id"]))
    if source is None:
        raise AppHTTPException(
            409,
            "WORKFLOW_SOURCE_EXCEL_FILE_MISSING",
            "冻结的 Excel 源文件已不可用。",
            {"file_id": params["file_id"]},
        )
    batch = workflow.input_batch
    source_item = next(
        item for item in batch.items if item.role == "source_excel" and item.file_id == source.id
    )
    handoff = params["dxf_split_handoff"]
    assert isinstance(handoff, dict)
    drawings = handoff.get("drawings")
    no_split_candidates = handoff.get("mode") == "no_split_candidates"
    return {
        "ready": True,
        "source_file_id": source.id,
        "source_file_name": source.original_name,
        "input_contract_version": source_item.validation_contract_version,
        "split_run_id": handoff.get("split_run_id"),
        "official_pair_count": len(drawings) if isinstance(drawings, list) else 0,
        "checks": [
            {"code": "input_batch_frozen", "label": "冻结输入清单有效"},
            {"code": "source_excel_unique", "label": "唯一 Excel 来源一致"},
            {"code": "source_object_verified", "label": "对象摘要与冻结记录一致"},
            {"code": "excel_contract_verified", "label": "Excel 表结构符合输入合同"},
            {
                "code": "split_handoff_verified",
                "label": (
                    "分类结果无需拆板，已按空交接继续"
                    if no_split_candidates
                    else "正式拆板结果成对且可用"
                ),
            },
        ],
    }


def preflight_excel_stage2(
    db: Session,
    workflow: WorkflowRun,
    *,
    current_user: User,
) -> dict[str, object]:
    """Validate the exact Stage2 lineage without creating or binding a Job."""
    require_stage_execution(
        workflow,
        stage_code="excel_stage2",
        execution_kind="excel_stage2",
    )
    _, params = _prepare_excel_stage2(db, workflow, current_user)
    require_stage_inputs(workflow, "excel_stage2")
    source = db.get(StoredFile, int(params["stage1_excel_file_id"]))
    if source is None:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_STAGE1_FILE_UNAVAILABLE",
            "Excel 第一阶段正式结果文件已不可用。",
            {"file_id": params["stage1_excel_file_id"]},
        )
    bh_input_count = int(params["bh_input_count"])
    box_input_count = int(params.get("box_input_count", 0))
    return {
        "ready": True,
        "mode": (
            "bh_enhancement"
            if bh_input_count or box_input_count
            else "no_bh_inputs"
        ),
        "stage1_file_id": source.id,
        "stage1_file_name": source.original_name,
        "stage1_job_id": params["stage1_job_id"],
        "stage1_job_attempt": params["stage1_job_attempt"],
        "classification_run_id": params["classification_run_id"],
        "classification_job_id": params["classification_job_id"],
        "classification_job_attempt": params["classification_job_attempt"],
        "bh_input_count": bh_input_count,
        "box_input_count": box_input_count,
        "checks": [
            {"code": "stage1_job_verified", "label": "第一阶段正式任务来源一致"},
            {
                "code": "stage1_workbook_verified",
                "label": "第一阶段唯一正式 Excel 已登记且存储可用",
            },
            {
                "code": "classification_run_verified",
                "label": "当前分类账、正式任务与冻结输入一致",
            },
            {
                "code": "bh_ledger_verified",
                "label": "BH 文件登记账本已冻结；逐图读取将在正式任务中执行",
            },
            {
                "code": "bh_batch_frozen",
                "label": (
                    f"已冻结 {bh_input_count} 张拆板前 BH 图纸"
                    if bh_input_count
                    else "当前分类账无 BH 图纸，将原样生成第二阶段结果"
                ),
            },
            {
                "code": "box_batch_frozen",
                "label": (
                    f"已冻结 {box_input_count} 张拆板前 BOX 图纸"
                    if box_input_count
                    else "当前分类账无 BOX 图纸"
                ),
            },
        ],
    }


def preflight_excel_stage3(
    db: Session,
    workflow: WorkflowRun,
    *,
    current_user: User,
) -> dict[str, object]:
    """Validate the exact Stage3 lineage without creating or binding a Job."""
    require_stage_execution(
        workflow,
        stage_code="excel_stage3",
        execution_kind="excel_stage3",
    )
    _, params = _prepare_excel_stage3(db, workflow, current_user)
    require_stage_inputs(workflow, "excel_stage3")
    stage2_stored = db.get(StoredFile, int(params["stage2_excel_file_id"]))
    if stage2_stored is None:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE3_STAGE2_FILE_UNAVAILABLE",
            "Excel 第二阶段正式结果文件已不可用。",
            {"file_id": params["stage2_excel_file_id"]},
        )
    dxf_file_ids = params["processed_dxf_file_ids"]
    assert isinstance(dxf_file_ids, list)
    dxf_count = len(dxf_file_ids)
    for dxf_id in dxf_file_ids:
        dxf_stored = db.get(StoredFile, int(dxf_id))
        if dxf_stored is None or dxf_stored.status == "deleted":
            raise AppHTTPException(
                409,
                "EXCEL_STAGE3_DXF_FILE_UNAVAILABLE",
                "拆板结果 DXF 文件已不可用。",
                {"file_id": dxf_id},
            )
    return {
        "ready": True,
        "stage2_file_name": stage2_stored.original_name,
        "stage2_file_id": stage2_stored.id,
        "dxf_count": dxf_count,
        "checks": [
            {"code": "stage2_artifact_verified", "label": "第二阶段正式 Excel 已登记且存储可用"},
            {"code": "dxf_inputs_verified", "label": f"已冻结 {dxf_count} 张拆板后 DXF"},
        ],
    }


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
                current.job.task_type not in {TASK_PL_DXF_SPLIT, TASK_STEEL_DXF_SPLIT}
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
                stage_code=(
                    "pl_xbox_split"
                    if current.job.task_type == TASK_PL_DXF_SPLIT
                    else "drawing_processing"
                ),
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
    if payload.execution_kind == "excel_stage1":
        require_stage_inputs(workflow, stage_code)
        task_type, params = _prepare_excel_stage1(db, workflow, current_user)
    elif payload.execution_kind == "excel_stage2":
        task_type, params = _prepare_excel_stage2(db, workflow, current_user)
        require_stage_inputs(workflow, stage_code)
    elif payload.execution_kind == "steel_dxf_classification":
        require_stage_inputs(workflow, stage_code)
        task_type, params = _prepare_dxf_classification(db, workflow, current_user)
    elif payload.execution_kind == "drawing_processing":
        require_stage_inputs(workflow, stage_code)
        task_type, params = _prepare_dxf_splitting(db, workflow, current_user)
    elif payload.execution_kind == "pl_xbox_split":
        require_stage_inputs(workflow, stage_code)
        task_type, params = _prepare_pl_dxf_splitting(db, workflow, current_user)
    elif payload.execution_kind == "excel_stage3":
        task_type, params = _prepare_excel_stage3(db, workflow, current_user)
        require_stage_inputs(workflow, stage_code)
    else:
        raise AppHTTPException(
            501,
            "WORKFLOW_STAGE_NOT_IMPLEMENTED",
            "This workflow stage has an API contract but no server implementation yet.",
            {"stage_code": stage_code, "execution_kind": payload.execution_kind},
        )

    binding_errors = {
        "drawing_processing": (
            "DXF_SPLIT_JOB_BINDING_INVALID",
            "当前拆板阶段绑定的 Job 与工作流冻结输入不一致。",
        ),
        "pl_xbox_split": (
            "PL_DXF_SPLIT_JOB_BINDING_INVALID",
            "当前 PL 拆板阶段绑定的 Job 与工作流冻结输入不一致。",
        ),
        "excel_stage2": (
            "EXCEL_STAGE2_JOB_BINDING_INVALID",
            "Excel 第二阶段绑定的任务与当前冻结输入不一致。",
        ),
        "excel_stage3": (
            "EXCEL_STAGE3_JOB_BINDING_INVALID",
            "Excel 第三阶段绑定的任务与当前冻结输入不一致。",
        ),
    }
    error_code, error_message = binding_errors.get(
        stage_code,
        (
            "WORKFLOW_STAGE_JOB_BINDING_INVALID",
            "当前自动阶段绑定的 Job 与工作流冻结输入不一致。",
        ),
    )
    job = _bound_stage_job(
        db,
        workflow,
        stage_code=stage_code,
        task_type=task_type,
        params=params,
        error_code=error_code,
        error_message=error_message,
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
        if (
            stage_code in {"drawing_processing", "pl_xbox_split"}
            and job.attempt >= MAX_AUTOMATIC_ATTEMPTS
        ):
            raise AppHTTPException(
                409,
                "DXF_SPLIT_ATTEMPTS_EXHAUSTED",
                "拆板任务只允许一次完整批次尝试，不能自动重跑。",
                {"job_id": job.id, "attempt": job.attempt},
            )
        job = retry_job(db, job)
    bind_stage_job(db, workflow, stage_code=stage_code, job=job)
    stage = next(item for item in workflow.stages if item.stage_code == stage_code)
    stage.input_json = params
    return StageExecutionPlan(job=job, reused=reused, retried=retried)


def _bound_stage_job(
    db: Session,
    workflow: WorkflowRun,
    *,
    stage_code: str,
    task_type: str,
    params: dict[str, object],
    error_code: str,
    error_message: str,
) -> Job | None:
    """Reuse only the Job already bound to this exact immutable stage input."""
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
        or (stage.input_json is not None and stage.input_json != params)
    ):
        raise AppHTTPException(
            409,
            error_code,
            error_message,
            {
                "workflow_id": workflow.id,
                "stage_job_id": stage.job_id,
                "stage_job_attempt": stage.job_attempt,
            },
        )
    return job


def _resolve_verified_source_excel(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
    *,
    enforce_file_access: bool = True,
) -> tuple[StoredFile, WorkflowInputItem, WorkflowInputBatch]:
    """Resolve and revalidate the one frozen source workbook for this workflow."""
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
    if enforce_file_access:
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
    return stored, item, batch


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
    stored, _item, batch = _resolve_verified_source_excel(
        db,
        workflow,
        current_user,
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


def _prepare_excel_stage2(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
) -> tuple[str, dict[str, object]]:
    """Freeze the current formal Stage1 workbook and BH classification ledger."""
    # The execution route already enforces a writable project role. The frozen
    # input item and source artifact below are project-owned workflow records,
    # so a later project member need not be the original uploader.
    source_excel, source_item, source_batch = _resolve_verified_source_excel(
        db,
        workflow,
        current_user,
        enforce_file_access=False,
    )

    stage1 = next(
        (stage for stage in workflow.stages if stage.stage_code == "excel_stage1"),
        None,
    )
    stage1_job = db.get(Job, stage1.job_id) if stage1 is not None and stage1.job_id else None
    if (
        stage1 is None
        or stage1.status != "succeeded"
        or stage1.job_id is None
        or stage1.job_attempt is None
        or stage1_job is None
        or stage1_job.project_id != workflow.project_id
        or stage1_job.task_type != TASK_EXCEL_FINAL
        or stage1_job.status != "succeeded"
        or stage1_job.attempt != stage1.job_attempt
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_STAGE1_BINDING_INVALID",
            "Excel 第一阶段尚未形成与当前项目和 attempt 一致的正式结果。",
            {
                "stage_job_id": stage1.job_id if stage1 is not None else None,
                "stage_job_attempt": stage1.job_attempt if stage1 is not None else None,
            },
        )

    current_artifacts = []
    for artifact in workflow.artifacts:
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
        if (
            artifact.stage_run_id == stage1.id
            and artifact.artifact_type == "stage1_excel"
            and metadata.get("job_id") == stage1_job.id
            and metadata.get("job_attempt") == stage1_job.attempt
        ):
            current_artifacts.append(artifact)
    if len(current_artifacts) != 1:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_STAGE1_BINDING_INVALID",
            "Excel 第一阶段当前 attempt 必须且只能有一个正式结果。",
            {
                "stage1_job_id": stage1_job.id,
                "stage1_job_attempt": stage1_job.attempt,
                "artifact_count": len(current_artifacts),
            },
        )
    artifact = current_artifacts[0]
    result = db.get(AnalysisResult, artifact.result_id) if artifact.result_id else None
    stored = db.get(StoredFile, artifact.file_id) if artifact.file_id else None
    result_metadata = (
        result.result_json
        if result is not None and isinstance(result.result_json, dict)
        else {}
    )
    if (
        result is None
        or result.job_id != stage1_job.id
        or result.result_type != TASK_EXCEL_FINAL
        or result.status != "succeeded"
        or result.result_file_id != artifact.file_id
        or result_metadata.get("workflow_artifact_type") != "stage1_excel"
        or result_metadata.get("job_attempt") != stage1_job.attempt
        or stored is None
        or stored.status != "available"
        or stored.file_ext.casefold() != ".xlsx"
        or not stored.sha256
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_STAGE1_BINDING_INVALID",
            "Excel 第一阶段的产物、分析结果和正式文件来源链不一致。",
            {
                "artifact_id": artifact.id,
                "artifact_file_id": artifact.file_id,
                "result_id": artifact.result_id,
            },
        )
    try:
        stage1_object = registration.get_storage_backend().stat_object(
            stored.bucket,
            stored.storage_key,
        )
    except StorageObjectNotFound as exc:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_STAGE1_FILE_UNAVAILABLE",
            "Excel 第一阶段正式结果文件已从文件存储中丢失。",
            {"file_id": stored.id},
        ) from exc
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE2_STAGE1_STORAGE_UNAVAILABLE",
            "当前无法核验 Excel 第一阶段正式结果文件，请稍后刷新状态。",
            {"file_id": stored.id},
        ) from exc
    if stage1_object.size_bytes != stored.size_bytes:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_STAGE1_FILE_UNAVAILABLE",
            "Excel 第一阶段正式结果文件与系统登记大小不一致。",
            {"file_id": stored.id},
        )

    try:
        classification = load_bh_stage2_classification_batch(db, workflow.id)
    except ClassificationError as exc:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_CLASSIFICATION_INPUT_INVALID",
            f"BH 左右进处理无法使用当前分类结果：{exc}",
        ) from exc
    classification_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == "dxf_classification"),
        None,
    )
    classification_job = db.get(Job, classification.classification_job_id)
    classification_params = (
        classification_job.params_json
        if classification_job is not None and isinstance(classification_job.params_json, dict)
        else {}
    )
    if (
        classification.workflow_run_id != workflow.id
        or classification.project_id != workflow.project_id
        or classification_stage is None
        or classification_stage.status != "succeeded"
        or classification_stage.job_id != classification.classification_job_id
        or classification_stage.job_attempt != classification.classification_job_attempt
        or classification_job is None
        or classification_job.project_id != workflow.project_id
        or classification_job.task_type != TASK_STEEL_DXF_CLASSIFICATION
        or classification_job.status != "succeeded"
        or classification_job.attempt != classification.classification_job_attempt
        or classification_params.get("workflow_id") != workflow.id
        or classification_params.get("input_manifest_sha256")
        != classification.input_manifest_sha256
        or classification.input_manifest_sha256 != source_batch.manifest_sha256
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_CLASSIFICATION_BINDING_INVALID",
            "当前 BH 分类账没有绑定本项目已成功的正式分类 Job attempt。",
            {
                "classification_run_id": classification.classification_run_id,
                "classification_job_id": classification.classification_job_id,
                "classification_job_attempt": classification.classification_job_attempt,
            },
        )

    try:
        box_classification = load_box_stage2_classification_batch(
            db,
            workflow.id,
        )
    except ClassificationError as exc:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_CLASSIFICATION_INPUT_INVALID",
            f"BOX 左右进处理无法使用当前分类结果：{exc}",
        ) from exc
    if (
        box_classification.workflow_run_id != workflow.id
        or box_classification.project_id != workflow.project_id
        or box_classification.classification_job_id
        != classification.classification_job_id
        or box_classification.classification_job_attempt
        != classification.classification_job_attempt
        or box_classification.input_manifest_sha256
        != classification.input_manifest_sha256
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE2_CLASSIFICATION_BINDING_INVALID",
            "当前 BOX 分类账没有绑定本项目已成功的正式分类 Job attempt。",
            {
                "classification_run_id": box_classification.classification_run_id,
                "classification_job_id": box_classification.classification_job_id,
                "classification_job_attempt": box_classification.classification_job_attempt,
            },
        )

    return TASK_EXCEL_STAGE2, {
        "workflow_id": workflow.id,
        "project_id": workflow.project_id,
        "source_excel_file_id": source_excel.id,
        "source_excel_sha256": source_item.validated_sha256,
        "stage1_artifact_id": artifact.id,
        "stage1_result_id": result.id,
        "stage1_excel_file_id": stored.id,
        "stage1_excel_sha256": stored.sha256,
        "stage1_job_id": stage1_job.id,
        "stage1_job_attempt": stage1_job.attempt,
        "classification_run_id": classification.classification_run_id,
        "classification_job_id": classification.classification_job_id,
        "classification_job_attempt": classification.classification_job_attempt,
        "classification_manifest_sha256": classification.input_manifest_sha256,
        "classifier_version": classification.classifier_version,
        "bh_input_count": len(classification.items),
        "bh_manifest_version": classification.bh_manifest_version,
        "bh_manifest_sha256": classification.bh_manifest_sha256,
        "box_classification_run_id": box_classification.classification_run_id,
        "box_classification_job_id": box_classification.classification_job_id,
        "box_classification_job_attempt": box_classification.classification_job_attempt,
        "box_input_count": len(box_classification.items),
        "box_manifest_version": box_classification.bh_manifest_version,
        "box_manifest_sha256": box_classification.bh_manifest_sha256,
    }


def _prepare_excel_stage3(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
) -> tuple[str, dict[str, object]]:
    """Freeze the Stage2 output Excel and drawing_processing DXF artifacts."""

    # Resolve Stage 2 output
    stage2 = next(
        (stage for stage in workflow.stages if stage.stage_code == "excel_stage2"),
        None,
    )
    stage2_job = db.get(Job, stage2.job_id) if stage2 is not None and stage2.job_id else None
    if (
        stage2 is None
        or stage2.status != "succeeded"
        or stage2.job_id is None
        or stage2.job_attempt is None
        or stage2_job is None
        or stage2_job.project_id != workflow.project_id
        or stage2_job.task_type != TASK_EXCEL_STAGE2
        or stage2_job.status != "succeeded"
        or stage2_job.attempt != stage2.job_attempt
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE3_STAGE2_BINDING_INVALID",
            "Excel 第二阶段尚未形成与当前项目和 attempt 一致的正式结果。",
            {
                "stage_job_id": stage2.job_id if stage2 is not None else None,
                "stage_job_attempt": stage2.job_attempt if stage2 is not None else None,
            },
        )

    stage2_artifacts = [
        artifact
        for artifact in workflow.artifacts
        if artifact.stage_run_id == stage2.id
        and artifact.artifact_type == "stage2_excel"
    ]
    if len(stage2_artifacts) != 1:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE3_STAGE2_ARTIFACT_INVALID",
            "Excel 第二阶段必须且只能有一个 stage2_excel 产物。",
            {"artifact_count": len(stage2_artifacts)},
        )
    stage2_artifact = stage2_artifacts[0]
    stage2_result = db.get(AnalysisResult, stage2_artifact.result_id) if stage2_artifact.result_id else None
    stage2_stored = db.get(StoredFile, stage2_artifact.file_id) if stage2_artifact.file_id else None
    if (
        stage2_result is None
        or stage2_result.job_id != stage2_job.id
        or stage2_result.status != "succeeded"
        or stage2_stored is None
        or stage2_stored.status != "available"
        or stage2_stored.file_ext.casefold() != ".xlsx"
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE3_STAGE2_BINDING_INVALID",
            "Excel 第二阶段的产物、分析结果和正式文件来源链不一致。",
            {
                "artifact_id": stage2_artifact.id,
                "artifact_file_id": stage2_artifact.file_id,
            },
        )

    # Resolve drawing_processing outputs (processed_dxf only)
    drawing_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == "drawing_processing"),
        None,
    )
    drawing_job = db.get(Job, drawing_stage.job_id) if drawing_stage is not None and drawing_stage.job_id else None
    if (
        drawing_stage is None
        or drawing_stage.status not in ("succeeded", "waiting_review")
        or drawing_stage.job_id is None
        or drawing_stage.job_attempt is None
        or drawing_job is None
        or drawing_job.project_id != workflow.project_id
        or drawing_job.task_type != TASK_STEEL_DXF_SPLIT
        or drawing_job.status not in ("succeeded", "need_review")
        or drawing_job.attempt != drawing_stage.job_attempt
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE3_DRAWING_BINDING_INVALID",
            "拆板阶段尚未形成与当前项目和 attempt 一致的正式结果。",
            {
                "stage_job_id": drawing_stage.job_id if drawing_stage is not None else None,
                "stage_job_attempt": drawing_stage.job_attempt if drawing_stage is not None else None,
            },
        )

    processed_dxf_artifacts = [
        artifact
        for artifact in workflow.artifacts
        if artifact.stage_run_id == drawing_stage.id
        and artifact.artifact_type == "processed_dxf"
        and isinstance(artifact.metadata_json, dict)
        and artifact.metadata_json.get("job_id") == drawing_job.id
        and artifact.metadata_json.get("job_attempt") == drawing_job.attempt
        and artifact.file_id is not None
    ]

    processed_dxf_file_ids: list[int] = []
    for artifact in processed_dxf_artifacts:
        stored = db.get(StoredFile, artifact.file_id)
        if stored is not None and stored.status != "deleted":
            processed_dxf_file_ids.append(stored.id)

    return TASK_EXCEL_STAGE3, {
        "workflow_id": workflow.id,
        "project_id": workflow.project_id,
        "stage2_excel_file_id": stage2_stored.id,
        "processed_dxf_file_ids": processed_dxf_file_ids,
        "stage2_job_id": stage2_job.id,
        "stage2_job_attempt": stage2_job.attempt,
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
        if stored is None or stored.status == "deleted" or stored.file_ext.casefold() != ".dxf":
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


def _prepare_pl_dxf_splitting(
    db: Session,
    workflow: WorkflowRun,
    current_user: User,
) -> tuple[str, dict[str, object]]:
    del current_user
    if not settings.dxf_split_pipeline_enabled:
        raise service_unavailable(
            "PL_DXF_SPLIT_PIPELINE_DISABLED",
            "PL 拆板服务当前未启用；部署和人工验证完成后再打开开关。",
        )
    classification = latest_classification_run(db, workflow.id)
    if classification is None:
        raise AppHTTPException(
            409,
            "DXF_CLASSIFICATION_RUN_REQUIRED",
            "PL 拆板前必须存在已完成的 DXF 分类运行。",
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
        )
    inputs = list_pl_split_candidate_inputs(db, workflow.id)
    xbox_inputs = list_xbox_split_candidate_inputs(db, workflow.id)
    if not inputs and not xbox_inputs:
        raise AppHTTPException(
            409,
            "PL_XBOX_SPLIT_INPUT_REQUIRED",
            "分类运行没有可供 PL/XBOX Stage 拆板的 DXF。",
        )
    for item in [*inputs, *xbox_inputs]:
        stored = db.get(StoredFile, item.output_file_id)
        if stored is None or stored.status == "deleted" or stored.file_ext.casefold() != ".dxf":
            raise AppHTTPException(
                409,
                "PL_DXF_SPLIT_SOURCE_MISSING",
                "分类后的 PL/XBOX 拆板输入已不可用。",
                {
                    "classification_item_id": item.classification_item_id,
                    "file_id": item.output_file_id,
                },
            )
    return TASK_PL_DXF_SPLIT, {
        "workflow_id": workflow.id,
        "classification_run_id": classification.id,
        "input_manifest_sha256": classification.input_manifest_sha256,
    }
