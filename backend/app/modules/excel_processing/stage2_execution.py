"""Attempt-aware BH Reader and Excel Stage2 worker orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from bh_reader.analyzer import BHAnalyzer
from bh_reader.batch import BhBatchOutcome, BhInputEntry, analyze_manifest
from bh_reader.simple_xlsx import write_results_xlsx
from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    DxfBhStage2ClassificationBatch,
    load_bh_stage2_classification_batch,
    load_box_stage2_classification_batch,
)
from app.modules.excel_processing.persistence import (
    cleanup_excel_processing_rows,
    import_workbook_for_job,
)
from app.modules.excel_processing.stage_adapter import run_excel_stage2_pipeline
from app.modules.files.interface import (
    StoredFile,
    complete_transfer_in_transaction,
    get_storage_backend,
    prepare_generated_file_transfer,
    save_bytes_as_file,
    session_factory_for,
    settle_transfer,
)
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    JobStep,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    fail_job_attempt,
    make_event,
)
from app.modules.workflows.interface import WorkflowArtifact, WorkflowRun
from app.platform.config.constants import (
    EXCEL_FILE_EXTENSIONS,
    JOB_RUNNING,
    PIPELINE_EXCEL_STAGE2,
    STEP_IMPORT_EXCEL_STAGE2_DB,
    STEP_PERSIST_EXCEL_STAGE2,
    STEP_RUN_BH_SETBACK_READER,
    STEP_RUN_EXCEL_STAGE2,
    STEP_VALIDATE_EXCEL_STAGE2_INPUTS,
    TASK_EXCEL_FINAL,
    TASK_EXCEL_STAGE2,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.storage.base import StorageObjectNotFound
from app.platform.time import business_now

logger = logging.getLogger(__name__)

_EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ALGORITHM_VERSION = "excel_stage2_bh/1"
_PARAM_FIELDS = frozenset({
    "workflow_id",
    "project_id",
    "source_excel_file_id",
    "source_excel_sha256",
    "stage1_artifact_id",
    "stage1_result_id",
    "stage1_excel_file_id",
    "stage1_excel_sha256",
    "stage1_job_id",
    "stage1_job_attempt",
    "classification_run_id",
    "classification_job_id",
    "classification_job_attempt",
    "classification_manifest_sha256",
    "classifier_version",
    "bh_input_count",
    "bh_manifest_version",
    "bh_manifest_sha256",
    "box_classification_run_id",
    "box_classification_job_id",
    "box_classification_job_attempt",
    "box_input_count",
    "box_manifest_version",
    "box_manifest_sha256",
})


class Stage2WorkerError(RuntimeError):
    """A stable worker failure safe to expose to production operators."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Stage2AttemptInactive(RuntimeError):
    """The task lost ownership of its Job attempt and must stop silently."""


@dataclass(frozen=True, slots=True)
class ExcelStage2WorkerInputs:
    source_excel: StoredFile
    stage1_excel: StoredFile
    classification_batch: DxfBhStage2ClassificationBatch
    box_classification_batch: DxfBhStage2ClassificationBatch | None = None


@dataclass(frozen=True, slots=True)
class BhReaderArtifacts:
    workbook_path: Path
    measurements_path: Path
    processed_count: int
    ok_count: int
    failure_count: int


@dataclass(frozen=True, slots=True)
class BoxReaderArtifacts:
    workbook_path: Path
    measurements_path: Path
    processed_count: int
    ok_count: int
    failure_count: int


class Stage2ReaderBlockingError(Stage2WorkerError):
    """A batch-level Reader conflict with a downloadable diagnostic workbook."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostic: BhReaderArtifacts,
    ) -> None:
        self.diagnostic = diagnostic
        super().__init__(code, message)


def _positive_int(params: dict[str, object], field: str, *, allow_zero: bool = False) -> int:
    value = params.get(field)
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_JOB_PARAMS_INVALID",
            "Excel 第二阶段任务的冻结参数不完整，请重新运行。",
        )
    return value


def _required_text(params: dict[str, object], field: str) -> str:
    value = params.get(field)
    if not isinstance(value, str) or not value.strip():
        raise Stage2WorkerError(
            "EXCEL_STAGE2_JOB_PARAMS_INVALID",
            "Excel 第二阶段任务的冻结参数不完整，请重新运行。",
        )
    return value.strip()


def _require_registered_file(
    db: Session,
    *,
    file_id: int,
    expected_sha256: str,
    expected_extensions: frozenset[str],
    message: str,
) -> StoredFile:
    stored = db.get(StoredFile, file_id)
    if (
        stored is None
        or stored.status != "available"
        or stored.file_ext.casefold() not in expected_extensions
        or stored.sha256 != expected_sha256
    ):
        raise Stage2WorkerError("EXCEL_STAGE2_INPUT_OBJECT_CHANGED", message)
    return stored


def resolve_excel_stage2_worker_inputs(
    db: Session,
    job: Job,
) -> ExcelStage2WorkerInputs:
    """Revalidate every frozen DB binding before downloading a single byte."""
    params = job.params_json if isinstance(job.params_json, dict) else {}
    if frozenset(params) != _PARAM_FIELDS:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_JOB_PARAMS_INVALID",
            "Excel 第二阶段任务的冻结参数不完整，请重新运行。",
        )
    workflow_id = _positive_int(params, "workflow_id")
    project_id = _positive_int(params, "project_id")
    if job.project_id != project_id:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_PROJECT_BINDING_INVALID",
            "Excel 第二阶段任务与当前项目不一致。",
        )
    workflow = db.get(WorkflowRun, workflow_id)
    if workflow is None or workflow.project_id != project_id:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_PROJECT_BINDING_INVALID",
            "Excel 第二阶段任务与当前工作流项目不一致。",
        )
    stage2 = next(
        (stage for stage in workflow.stages if stage.stage_code == "excel_stage2"),
        None,
    )
    if (
        stage2 is None
        or stage2.job_id != job.id
        or stage2.job_attempt != job.attempt
    ):
        raise Stage2WorkerError(
            "EXCEL_STAGE2_JOB_BINDING_INVALID",
            "Excel 第二阶段任务不是当前工作流登记的正式 attempt。",
        )

    source_file_id = _positive_int(params, "source_excel_file_id")
    source_sha256 = _required_text(params, "source_excel_sha256")
    source = _require_registered_file(
        db,
        file_id=source_file_id,
        expected_sha256=source_sha256,
        expected_extensions=EXCEL_FILE_EXTENSIONS,
        message="冻结的原始 Excel 已发生变化，请重新执行生产流程。",
    )
    source_items = [
        item
        for item in workflow.input_batch.items
        if item.role == "source_excel"
    ] if workflow.input_batch is not None else []
    source_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == "source_intake"),
        None,
    )
    source_artifacts = [
        artifact
        for artifact in workflow.artifacts
        if source_stage is not None
        and artifact.stage_run_id == source_stage.id
        and artifact.artifact_type == "source_excel"
    ]
    if (
        workflow.input_batch is None
        or workflow.input_batch.status != "frozen"
        or len(source_items) != 1
        or source_items[0].file_id != source.id
        or source_items[0].validated_sha256 != source_sha256
        or len(source_artifacts) != 1
        or source_artifacts[0].file_id != source.id
    ):
        raise Stage2WorkerError(
            "EXCEL_STAGE2_SOURCE_BINDING_INVALID",
            "冻结原始 Excel 的工作流来源链不一致。",
        )

    stage1_job_id = _positive_int(params, "stage1_job_id")
    stage1_attempt = _positive_int(params, "stage1_job_attempt")
    stage1_file_id = _positive_int(params, "stage1_excel_file_id")
    stage1_sha256 = _required_text(params, "stage1_excel_sha256")
    stage1_job = db.get(Job, stage1_job_id)
    stage1_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == "excel_stage1"),
        None,
    )
    artifact = db.get(WorkflowArtifact, _positive_int(params, "stage1_artifact_id"))
    result = db.get(AnalysisResult, _positive_int(params, "stage1_result_id"))
    metadata = artifact.metadata_json if artifact is not None and isinstance(
        artifact.metadata_json, dict
    ) else {}
    if (
        stage1_job is None
        or stage1_job.project_id != project_id
        or stage1_job.task_type != TASK_EXCEL_FINAL
        or stage1_job.status != "succeeded"
        or stage1_job.attempt != stage1_attempt
        or stage1_stage is None
        or stage1_stage.job_id != stage1_job.id
        or stage1_stage.job_attempt != stage1_attempt
        or stage1_stage.status != "succeeded"
        or artifact is None
        or artifact.workflow_run_id != workflow.id
        or artifact.stage_run_id != stage1_stage.id
        or artifact.artifact_type != "stage1_excel"
        or artifact.file_id != stage1_file_id
        or metadata.get("job_id") != stage1_job.id
        or metadata.get("job_attempt") != stage1_attempt
        or result is None
        or result.id != artifact.result_id
        or result.job_id != stage1_job.id
        or result.result_file_id != stage1_file_id
        or result.status != "succeeded"
    ):
        raise Stage2WorkerError(
            "EXCEL_STAGE2_STAGE1_BINDING_INVALID",
            "Excel 第一阶段正式结果的来源链已发生变化。",
        )
    stage1 = _require_registered_file(
        db,
        file_id=stage1_file_id,
        expected_sha256=stage1_sha256,
        expected_extensions=frozenset({".xlsx"}),
        message="Excel 第一阶段正式结果已发生变化，请重新运行。",
    )

    classification_run_id = _positive_int(params, "classification_run_id")
    try:
        batch = load_bh_stage2_classification_batch(
            db,
            workflow.id,
            expected_run_id=classification_run_id,
        )
    except Exception as exc:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_INPUT_MANIFEST_CHANGED",
            "BH 图纸清单在任务启动后发生变化，请重新运行。",
        ) from exc
    classification_job = db.get(Job, batch.classification_job_id)
    classification_stage = next(
        (
            stage
            for stage in workflow.stages
            if stage.stage_code == "dxf_classification"
        ),
        None,
    )
    if (
        batch.workflow_run_id != workflow.id
        or batch.project_id != project_id
        or classification_job is None
        or classification_job.project_id != project_id
        or classification_job.task_type != TASK_STEEL_DXF_CLASSIFICATION
        or classification_job.status != "succeeded"
        or classification_job.id != _positive_int(params, "classification_job_id")
        or classification_job.attempt
        != _positive_int(params, "classification_job_attempt")
        or classification_stage is None
        or classification_stage.job_id != classification_job.id
        or classification_stage.job_attempt != classification_job.attempt
        or classification_stage.status != "succeeded"
        or batch.input_manifest_sha256
        != _required_text(params, "classification_manifest_sha256")
        or batch.classifier_version != _required_text(params, "classifier_version")
        or len(batch.items) != _positive_int(params, "bh_input_count", allow_zero=True)
        or batch.bh_manifest_version
        != _positive_int(params, "bh_manifest_version")
        or batch.bh_manifest_sha256 != _required_text(params, "bh_manifest_sha256")
    ):
        raise Stage2WorkerError(
            "EXCEL_STAGE2_INPUT_MANIFEST_CHANGED",
            "BH 图纸清单在任务启动后发生变化，请重新运行。",
        )
    box_batch: DxfBhStage2ClassificationBatch | None = None
    if "box_classification_run_id" in params:
        box_run_id = _positive_int(params, "box_classification_run_id")
        box_batch = load_box_stage2_classification_batch(
            db,
            workflow.id,
            expected_run_id=box_run_id,
        )
        box_job = db.get(Job, _positive_int(params, "box_classification_job_id"))
        if (
            box_batch.workflow_run_id != workflow.id
            or box_batch.project_id != project_id
            or box_job is None
            or box_job.project_id != project_id
            or box_job.status != "succeeded"
            or box_job.attempt != _positive_int(params, "box_classification_job_attempt")
            or len(box_batch.items)
            != _positive_int(params, "box_input_count", allow_zero=True)
            or box_batch.bh_manifest_version
            != _positive_int(params, "box_manifest_version")
            or box_batch.bh_manifest_sha256
            != _required_text(params, "box_manifest_sha256")
        ):
            raise Stage2WorkerError(
                "EXCEL_STAGE2_INPUT_MANIFEST_CHANGED",
                "BOX 图纸清单在任务启动后发生变化，请重新运行。",
            )
    return ExcelStage2WorkerInputs(
        source_excel=source,
        stage1_excel=stage1,
        classification_batch=batch,
        box_classification_batch=box_batch,
    )


def stage_registered_file(
    stored: StoredFile,
    destination: Path,
    expected_sha256: str,
) -> Path:
    """Stream one registered object to a private path and verify its bytes."""
    if stored.status != "available" or stored.sha256 != expected_sha256:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_INPUT_OBJECT_CHANGED",
            f"输入文件 {stored.original_name} 的登记摘要已变化。",
        )
    return _stage_storage_object(
        bucket=stored.bucket,
        storage_key=stored.storage_key,
        original_name=stored.original_name,
        size_bytes=stored.size_bytes,
        expected_sha256=expected_sha256,
        destination=destination,
    )


def _stage_storage_object(
    *,
    bucket: str,
    storage_key: str,
    original_name: str,
    size_bytes: int,
    expected_sha256: str,
    destination: Path,
) -> Path:
    storage = get_storage_backend()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    staged_size = 0
    try:
        local = storage.local_path(bucket, storage_key)
        if local is not None:
            if not local.is_file():
                raise StorageObjectNotFound(f"{bucket}/{storage_key}")
            with local.open("rb") as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    staged_size += len(chunk)
        else:
            with temporary.open("wb") as output:
                for chunk in storage.iter_file(bucket, storage_key):
                    output.write(chunk)
                    digest.update(chunk)
                    staged_size += len(chunk)
        if digest.hexdigest() != expected_sha256 or staged_size != size_bytes:
            raise Stage2WorkerError(
                "EXCEL_STAGE2_INPUT_OBJECT_CHANGED",
                f"输入文件 {original_name} 的存储内容已变化。",
            )
        temporary.replace(destination)
        return destination
    except StorageObjectNotFound as exc:
        raise Stage2WorkerError(
            "EXCEL_STAGE2_INPUT_OBJECT_MISSING",
            f"输入文件 {original_name} 已从存储器中丢失。",
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def run_bh_reader_batch(
    db: Session,
    job: Job,
    inputs: ExcelStage2WorkerInputs,
    work_dir: Path,
    progress,
) -> BhReaderArtifacts:
    """Download and analyze one drawing at a time, retaining compact results only."""
    del db, job
    analyzer = BHAnalyzer()
    batch_items = []
    contract_items: list[dict[str, object]] = []
    part_number_sources: dict[str, str] = {}
    duplicate_part_numbers: list[tuple[str, str, str]] = []
    total = len(inputs.classification_batch.items)
    input_dir = work_dir / "bh-input"
    for processed, item in enumerate(inputs.classification_batch.items, start=1):
        physical_path = input_dir / f"{item.classification_item_id}.dxf"
        _stage_storage_object(
            bucket=item.input_bucket,
            storage_key=item.input_storage_key,
            original_name=item.input_name,
            size_bytes=item.input_size_bytes,
            expected_sha256=item.input_sha256,
            destination=physical_path,
        )
        try:
            outcome = analyze_manifest(
                (BhInputEntry(path=physical_path, file_name=item.input_name),),
                backend="auto",
                on_progress=lambda _value: None,
                analyzer=analyzer,
            )
        finally:
            physical_path.unlink(missing_ok=True)
        result = outcome.items[0]
        normalized_part_number = result.part_number.strip().casefold()
        first_source = part_number_sources.get(normalized_part_number)
        if normalized_part_number and first_source is not None:
            duplicate_part_numbers.append(
                (result.part_number.strip(), first_source, item.input_name)
            )
        if normalized_part_number:
            part_number_sources.setdefault(normalized_part_number, item.input_name)
        batch_items.append(result)
        contract_items.append({
            "source_file_id": item.input_file_id,
            "file_name": item.input_name,
            "part_number": result.part_number,
            "classification_spec": item.profile_normalized,
            "reader_spec": result.specification,
            "status": result.status,
            "warnings": list(result.warnings),
            "measurements": [
                {
                    "role": measurement.role,
                    "left_safe": measurement.left_safe,
                    "right_safe": measurement.right_safe,
                }
                for measurement in result.measurements
            ],
        })
        progress(processed, total, item.input_name, result.status)

    outcome = BhBatchOutcome(tuple(batch_items))
    workbook_path = work_dir / "BH左右进读取表.xlsx"
    measurements_path = work_dir / "bh-measurements.json"
    write_results_xlsx(
        workbook_path,
        outcome.iter_result_rows(),
        outcome.iter_diagnostic_rows(),
    )
    measurements_path.write_text(
        json.dumps(
            {"schema": "bh_setback_measurements/v1", "items": contract_items},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    artifacts = BhReaderArtifacts(
        workbook_path=workbook_path,
        measurements_path=measurements_path,
        processed_count=outcome.processed_count,
        ok_count=outcome.ok_count,
        failure_count=outcome.failure_count,
    )
    if duplicate_part_numbers:
        part_number, first_source, conflicting_source = duplicate_part_numbers[0]
        extra_count = len(duplicate_part_numbers) - 1
        suffix = f"，另有 {extra_count} 处重复" if extra_count else ""
        raise Stage2ReaderBlockingError(
            "EXCEL_STAGE2_DUPLICATE_PART_NUMBER",
            (
                f"BH 零件号 {part_number} 同时出现在图纸 {first_source} 和 "
                f"{conflicting_source}{suffix}，无法唯一匹配整理表。"
            ),
            diagnostic=artifacts,
        )
    return artifacts


def run_box_reader_batch(
    db: Session,
    job: Job,
    inputs: ExcelStage2WorkerInputs,
    work_dir: Path,
    progress,
) -> BoxReaderArtifacts:
    """Download and analyze each BOX drawing, retaining compact results only."""
    from box_reader.analyzer import BoxAnalyzer
    from box_reader.batch import (
        BoxInputEntry,
    )
    from box_reader.batch import (
        analyze_manifest as analyze_box_manifest,
    )
    from box_reader.simple_xlsx import (
        write_results_xlsx as write_box_results_xlsx,
    )

    del db, job
    batch = inputs.box_classification_batch
    if batch is None or not batch.items:
        workbook_path = work_dir / "BOX左右进读取表.xlsx"
        measurements_path = work_dir / "box-measurements.json"
        workbook_path.write_bytes(b"")
        measurements_path.write_text(
            json.dumps(
                {"schema": "box_setback_measurements/v1", "items": []},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return BoxReaderArtifacts(
            workbook_path=workbook_path,
            measurements_path=measurements_path,
            processed_count=0,
            ok_count=0,
            failure_count=0,
        )

    analyzer = BoxAnalyzer()
    batch_items = []
    contract_items: list[dict[str, object]] = []
    part_number_sources: dict[str, str] = {}
    duplicate_part_numbers: list[tuple[str, str, str]] = []
    total = len(batch.items)
    input_dir = work_dir / "box-input"
    for processed, item in enumerate(batch.items, start=1):
        physical_path = input_dir / f"{item.classification_item_id}.dxf"
        _stage_storage_object(
            bucket=item.input_bucket,
            storage_key=item.input_storage_key,
            original_name=item.input_name,
            size_bytes=item.input_size_bytes,
            expected_sha256=item.input_sha256,
            destination=physical_path,
        )
        try:
            outcome = analyze_box_manifest(
                (BoxInputEntry(path=physical_path, file_name=item.input_name),),
                on_progress=lambda _value: None,
                analyzer=analyzer,
            )
        finally:
            physical_path.unlink(missing_ok=True)
        result = outcome.items[0]
        normalized_part_number = result.part_number.strip().casefold()
        first_source = part_number_sources.get(normalized_part_number)
        if normalized_part_number and first_source is not None:
            duplicate_part_numbers.append(
                (result.part_number.strip(), first_source, item.input_name)
            )
        if normalized_part_number:
            part_number_sources.setdefault(normalized_part_number, item.input_name)
        batch_items.append(result)
        contract_items.append({
            "source_file_id": item.input_file_id,
            "file_name": item.input_name,
            "part_number": result.part_number,
            "classification_spec": item.profile_normalized,
            "reader_spec": result.specification,
            "status": result.status,
            "warnings": list(result.warnings),
            "measurements": [
                {
                    "role": measurement.role,
                    "left_safe": measurement.left_safe,
                    "right_safe": measurement.right_safe,
                }
                for measurement in result.measurements
            ],
        })
        progress(processed, total, item.input_name, result.status)

    from box_reader.batch import BoxBatchOutcome

    outcome = BoxBatchOutcome(tuple(batch_items))
    workbook_path = work_dir / "BOX左右进读取表.xlsx"
    measurements_path = work_dir / "box-measurements.json"
    write_box_results_xlsx(
        workbook_path,
        outcome.iter_result_rows(),
        outcome.iter_diagnostic_rows(),
    )
    measurements_path.write_text(
        json.dumps(
            {"schema": "box_setback_measurements/v1", "items": contract_items},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    artifacts = BoxReaderArtifacts(
        workbook_path=workbook_path,
        measurements_path=measurements_path,
        processed_count=outcome.processed_count,
        ok_count=outcome.ok_count,
        failure_count=outcome.failure_count,
    )
    if duplicate_part_numbers:
        part_number, first_source, conflicting_source = duplicate_part_numbers[0]
        extra_count = len(duplicate_part_numbers) - 1
        suffix = f"，另有 {extra_count} 处重复" if extra_count else ""
        raise Stage2ReaderBlockingError(
            "EXCEL_STAGE2_DUPLICATE_PART_NUMBER",
            (
                f"BOX 零件号 {part_number} 同时出现在图纸 {first_source} 和 "
                f"{conflicting_source}{suffix}，无法唯一匹配整理表。"
            ),
            diagnostic=artifacts,
        )
    return artifacts


def _add_step(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    worker_name: str,
    step_name: str,
    status: str,
    started_at: datetime,
    input_json: dict[str, object] | None = None,
    output_json: dict[str, object] | None = None,
    error_message: str | None = None,
) -> None:
    db.add(JobStep(
        job_id=job_id,
        attempt=attempt,
        step_name=step_name,
        worker_name=worker_name,
        status=status,
        input_json=input_json,
        output_json=output_json,
        error_message=error_message,
        started_at=started_at,
        finished_at=business_now(),
    ))


def _commit_progress(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    progress: int,
    step_name: str,
    message: str,
    **data: object,
) -> Job:
    job = commit_job_progress(
        db,
        job_id,
        attempt=attempt,
        progress=progress,
        event=make_event(
            type_="progress",
            status=JOB_RUNNING,
            progress=progress,
            step_name=step_name,
            message=message,
            **data,
        ),
    )
    if job is None:
        raise Stage2AttemptInactive
    return job


def _reader_progress_callback(db: Session, *, job_id: int, attempt: int):
    last_time = 0.0
    last_processed = 0

    def report(processed: int, total: int, file_name: str, status: str) -> None:
        nonlocal last_time, last_processed
        now = time.monotonic()
        threshold = max(1, total // 200)
        if processed != total and now - last_time < 1.0 and processed - last_processed < threshold:
            return
        percentage = 15 + int(60 * processed / max(total, 1))
        _commit_progress(
            db,
            job_id=job_id,
            attempt=attempt,
            progress=percentage,
            step_name=STEP_RUN_BH_SETBACK_READER,
            message=f"正在读取 BH 图纸：{processed}/{total}",
            phase="bh_reader",
            processed_files=processed,
            total_files=total,
            current_file_name=file_name,
            reader_status=status,
        )
        last_time = now
        last_processed = processed

    return report


def _box_reader_progress_callback(db: Session, *, job_id: int, attempt: int):
    last_time = 0.0
    last_processed = 0

    def report(processed: int, total: int, file_name: str, status: str) -> None:
        nonlocal last_time, last_processed
        now = time.monotonic()
        threshold = max(1, total // 200)
        if processed != total and now - last_time < 1.0 and processed - last_processed < threshold:
            return
        # BH 读取占用 15-75；BOX 读取紧随其后占用 75-80（进度单调不回退）
        percentage = 75 + int(5 * processed / max(total, 1))
        _commit_progress(
            db,
            job_id=job_id,
            attempt=attempt,
            progress=percentage,
            step_name=STEP_RUN_BH_SETBACK_READER,
            message=f"正在读取 BOX 图纸：{processed}/{total}",
            phase="box_reader",
            processed_files=processed,
            total_files=total,
            current_file_name=file_name,
            reader_status=status,
        )
        last_time = now
        last_processed = processed

    return report


def _rebuild_progress_callback(db: Session, *, job_id: int, attempt: int):
    """Keep the active attempt alive while the isolated workbook rebuild runs."""

    def report() -> None:
        _commit_progress(
            db,
            job_id=job_id,
            attempt=attempt,
            progress=80,
            step_name=STEP_RUN_EXCEL_STAGE2,
            message="正在深化整理表和 part 表",
            phase="rebuild_excel",
            activity="running",
        )

    return report


def _persist_workbook(
    db: Session,
    *,
    job: Job,
    attempt: int,
    path: Path,
    original_name: str,
    artifact_type: str,
    result_payload: dict[str, object],
) -> AnalysisResult:
    payload = path.read_bytes()
    storage_key = f"jobs/{job.id}/attempt-{attempt}/{artifact_type}-{uuid4().hex}.xlsx"
    transfer_uid = prepare_generated_file_transfer(
        db,
        actor_user_id=job.created_by,
        request_id=f"job:{job.id}:attempt:{attempt}:{artifact_type}",
        batch_ref=None,
        bucket=settings.minio_bucket_reports,
        storage_key=storage_key,
        original_name=original_name,
        expected_bytes=len(payload),
    )
    active = db.get(Job, job.id, populate_existing=True)
    if active is None or active.status != JOB_RUNNING or active.attempt != attempt:
        db.rollback()
        settle_transfer(
            session_factory_for(db),
            transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code="JOB_ATTEMPT_INACTIVE",
            error_message="Job attempt changed before Excel Stage2 result persistence.",
        )
        raise Stage2AttemptInactive
    stored = save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_reports,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=".xlsx",
        content_type=_EXCEL_CONTENT_TYPE,
        payload=payload,
        uploaded_by=active.created_by,
        transfer_uid=transfer_uid,
    )
    complete_transfer_in_transaction(
        db,
        transfer_uid,
        file_id=stored.id,
        bucket=stored.bucket,
        storage_key=stored.storage_key,
        original_name=stored.original_name,
        transferred_bytes=stored.size_bytes,
    )
    analysis = AnalysisResult(
        job_id=active.id,
        drawing_id=active.drawing_id,
        result_type=TASK_EXCEL_STAGE2,
        result_json={
            **result_payload,
            "job_attempt": attempt,
            "workflow_artifact_type": artifact_type,
        },
        confidence=Decimal("1.0000"),
        result_file_id=stored.id,
        algorithm_version=_ALGORITHM_VERSION,
        tool_version="excel_stage2",
        status="succeeded",
    )
    db.add(analysis)
    db.commit()
    return analysis


def _mark_failed(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    error_code: str,
    error_message: str,
) -> None:
    active = db.get(Job, job_id, populate_existing=True)
    if active is None or active.attempt != attempt or active.status != JOB_RUNNING:
        return
    cleanup_excel_processing_rows(db, (job_id,))
    fail_job_attempt(
        db,
        job_id,
        attempt=attempt,
        error_code=error_code,
        error_message=error_message,
    )


def _mark_reader_result_failed(
    db: Session,
    *,
    result_id: int | None,
    error_code: str,
) -> None:
    if result_id is None:
        return
    result = db.get(AnalysisResult, result_id, populate_existing=True)
    if result is None:
        return
    result.result_json = {
        **dict(result.result_json or {}),
        "stage2_status": "failed",
        "diagnostic_only": True,
        "error_code": error_code,
    }


def run_excel_stage2_processing(
    job_id: int,
    worker_name: str = "celery_excel_stage2",
    expected_attempt: int = 1,
) -> None:
    """Execute one immutable BH Reader + Excel Stage2 Job attempt."""
    db = SessionLocal()
    work_dir: Path | None = None
    reader_result: AnalysisResult | None = None
    box_reader_result: AnalysisResult | None = None
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_EXCEL_STAGE2,
            progress=2,
            message="开始核验 Excel 第二阶段输入",
        )
        if job is None:
            return
        attempt = job.attempt
        validation_started = business_now()
        inputs = resolve_excel_stage2_worker_inputs(db, job)
        _add_step(
            db,
            job_id=job.id,
            attempt=attempt,
            worker_name=worker_name,
            step_name=STEP_VALIDATE_EXCEL_STAGE2_INPUTS,
            status="succeeded",
            started_at=validation_started,
            output_json={
                "bh_input_count": len(inputs.classification_batch.items),
                "bh_manifest_sha256": inputs.classification_batch.bh_manifest_sha256,
            },
        )
        job = _commit_progress(
            db,
            job_id=job.id,
            attempt=attempt,
            progress=8,
            step_name=STEP_VALIDATE_EXCEL_STAGE2_INPUTS,
            message="来源和冻结清单核验完成",
            phase="validate_inputs",
            bh_input_count=len(inputs.classification_batch.items),
        )

        work_dir = (
            settings.excel_stage2_work_root
            / str(inputs.classification_batch.workflow_run_id)
            / str(job.id)
            / f"attempt-{attempt}"
        )
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, mode=0o700)
        source_path = stage_registered_file(
            inputs.source_excel,
            work_dir / "source.xlsx",
            inputs.source_excel.sha256,
        )
        stage1_path = stage_registered_file(
            inputs.stage1_excel,
            work_dir / "stage1.xlsx",
            inputs.stage1_excel.sha256,
        )
        source_path.unlink(missing_ok=True)
        job = _commit_progress(
            db,
            job_id=job.id,
            attempt=attempt,
            progress=15,
            step_name=STEP_VALIDATE_EXCEL_STAGE2_INPUTS,
            message="已建立 BH 图纸读取清单",
            phase="stage_inputs",
            bh_input_count=len(inputs.classification_batch.items),
        )

        reader_started = business_now()
        try:
            reader = run_bh_reader_batch(
                db,
                job,
                inputs,
                work_dir,
                _reader_progress_callback(db, job_id=job.id, attempt=attempt),
            )
        except Stage2ReaderBlockingError as exc:
            reader_result = _persist_workbook(
                db,
                job=job,
                attempt=attempt,
                path=exc.diagnostic.workbook_path,
                original_name="BH左右进诊断表.xlsx",
                artifact_type="bh_setback_excel",
                result_payload={
                    "source": "bh_left_right_reader",
                    "stage2_status": "failed",
                    "diagnostic_only": True,
                    "error_code": exc.code,
                    "processed_count": exc.diagnostic.processed_count,
                    "ok_count": exc.diagnostic.ok_count,
                    "failure_count": exc.diagnostic.failure_count,
                },
            )
            raise
        _add_step(
            db,
            job_id=job.id,
            attempt=attempt,
            worker_name=worker_name,
            step_name=STEP_RUN_BH_SETBACK_READER,
            status="succeeded",
            started_at=reader_started,
            output_json={
                "processed_count": reader.processed_count,
                "ok_count": reader.ok_count,
                "failure_count": reader.failure_count,
            },
        )
        reader_result = _persist_workbook(
            db,
            job=job,
            attempt=attempt,
            path=reader.workbook_path,
            original_name="BH左右进读取表.xlsx",
            artifact_type="bh_setback_excel",
            result_payload={
                "source": "bh_left_right_reader",
                "stage2_status": "pending_excel",
                "processed_count": reader.processed_count,
                "ok_count": reader.ok_count,
                "failure_count": reader.failure_count,
            },
        )

        box_reader: BoxReaderArtifacts | None = None
        if inputs.box_classification_batch is not None and inputs.box_classification_batch.items:
            box_reader_started = business_now()
            try:
                box_reader = run_box_reader_batch(
                    db,
                    job,
                    inputs,
                    work_dir,
                    _box_reader_progress_callback(db, job_id=job.id, attempt=attempt),
                )
            except Stage2ReaderBlockingError as exc:
                box_reader_result = _persist_workbook(
                    db,
                    job=job,
                    attempt=attempt,
                    path=exc.diagnostic.workbook_path,
                    original_name="BOX左右进诊断表.xlsx",
                    artifact_type="box_setback_excel",
                    result_payload={
                        "source": "box_left_right_reader",
                        "stage2_status": "failed",
                        "diagnostic_only": True,
                        "error_code": exc.code,
                        "processed_count": exc.diagnostic.processed_count,
                        "ok_count": exc.diagnostic.ok_count,
                        "failure_count": exc.diagnostic.failure_count,
                    },
                )
                raise
            _add_step(
                db,
                job_id=job.id,
                attempt=attempt,
                worker_name=worker_name,
                step_name=STEP_RUN_BH_SETBACK_READER,
                status="succeeded",
                started_at=business_now(),
                output_json={
                    "reader": "box",
                    "processed_count": box_reader.processed_count,
                    "ok_count": box_reader.ok_count,
                    "failure_count": box_reader.failure_count,
                },
            )
            box_reader_result = _persist_workbook(
                db,
                job=job,
                attempt=attempt,
                path=box_reader.workbook_path,
                original_name="BOX左右进读取表.xlsx",
                artifact_type="box_setback_excel",
                result_payload={
                    "source": "box_left_right_reader",
                    "stage2_status": "pending_excel",
                    "processed_count": box_reader.processed_count,
                    "ok_count": box_reader.ok_count,
                    "failure_count": box_reader.failure_count,
                },
            )

        stage_started = business_now()
        output_name = f"{Path(inputs.stage1_excel.original_name).stem}_BH和BOX左右进处理后.xlsx"
        output_path = work_dir / "stage2.xlsx"
        stage_result = run_excel_stage2_pipeline(
            stage1_path,
            reader.measurements_path,
            output_path,
            box_measurements_path=(
                box_reader.measurements_path if box_reader is not None else None
            ),
            on_heartbeat=_rebuild_progress_callback(
                db,
                job_id=job.id,
                attempt=attempt,
            ),
        )
        _add_step(
            db,
            job_id=job.id,
            attempt=attempt,
            worker_name=worker_name,
            step_name=STEP_RUN_EXCEL_STAGE2,
            status="succeeded",
            started_at=stage_started,
            output_json={
                "stage2_status": stage_result.status,
                "matched_occurrence_count": stage_result.matched_occurrence_count,
                "missing_drawing_count": stage_result.missing_drawing_count,
                "unmatched_drawing_count": stage_result.unmatched_drawing_count,
                "manual_occurrence_count": stage_result.manual_occurrence_count,
            },
        )
        job = _commit_progress(
            db,
            job_id=job.id,
            attempt=attempt,
            progress=88,
            step_name=STEP_RUN_EXCEL_STAGE2,
            message="整理表和 part 深化完成",
            phase="rebuild_excel",
            stage2_status=stage_result.status,
        )

        import_started = business_now()
        batch, database_stats = import_workbook_for_job(
            db,
            job_id=job.id,
            file_id=inputs.stage1_excel.id,
            source_type="stage2_bh",
            source_name=inputs.stage1_excel.original_name,
            output_path=stage_result.internal_output_path,
            expected_quality={
                "quality_status": stage_result.quality_status,
                "warning_count": stage_result.warning_count,
                "severe_warning_count": stage_result.severe_warning_count,
            },
        )
        _add_step(
            db,
            job_id=job.id,
            attempt=attempt,
            worker_name=worker_name,
            step_name=STEP_IMPORT_EXCEL_STAGE2_DB,
            status="succeeded",
            started_at=import_started,
            output_json=database_stats,
        )
        job = _commit_progress(
            db,
            job_id=job.id,
            attempt=attempt,
            progress=96,
            step_name=STEP_IMPORT_EXCEL_STAGE2_DB,
            message="第二阶段结果复核与入库完成",
            phase="import_mysql",
            stage2_status=stage_result.status,
        )

        persist_started = business_now()
        result_payload = {
            "source": "excel_stage2_bh",
            "stage2_status": stage_result.status,
            "matched_occurrence_count": stage_result.matched_occurrence_count,
            "missing_drawing_count": stage_result.missing_drawing_count,
            "unmatched_drawing_count": stage_result.unmatched_drawing_count,
            "manual_occurrence_count": stage_result.manual_occurrence_count,
            "quality_status": stage_result.quality_status,
            "warning_count": stage_result.warning_count,
            "severe_warning_count": stage_result.severe_warning_count,
            "report_summary": stage_result.report_summary,
            **database_stats,
        }
        formal_result = _persist_workbook(
            db,
            job=job,
            attempt=attempt,
            path=stage_result.output_path,
            original_name=output_name,
            artifact_type="stage2_excel",
            result_payload=result_payload,
        )
        reader_result = db.get(AnalysisResult, reader_result.id, populate_existing=True)
        if reader_result is None:
            raise Stage2WorkerError(
                "EXCEL_STAGE2_RESULT_PERSIST_FAILED",
                "左右进读取表登记失败，未发布第二阶段结果。",
            )
        reader_result.result_json = {
            **dict(reader_result.result_json or {}),
            "stage2_status": stage_result.status,
        }
        if box_reader_result is not None:
            box_reader_result = db.get(
                AnalysisResult,
                box_reader_result.id,
                populate_existing=True,
            )
            if box_reader_result is None:
                raise Stage2WorkerError(
                    "EXCEL_STAGE2_RESULT_PERSIST_FAILED",
                    "BOX 左右进读取表登记失败，未发布第二阶段结果。",
                )
            box_reader_result.result_json = {
                **dict(box_reader_result.result_json or {}),
                "stage2_status": stage_result.status,
            }
        _add_step(
            db,
            job_id=job.id,
            attempt=attempt,
            worker_name=worker_name,
            step_name=STEP_PERSIST_EXCEL_STAGE2,
            status="succeeded",
            started_at=persist_started,
            output_json={
                "reader_result_id": reader_result.id,
                "stage2_result_id": formal_result.id,
                "stage2_status": stage_result.status,
            },
        )
        complete_job_attempt(
            db,
            job.id,
            attempt=attempt,
            event=make_event(
                type_="done",
                status="succeeded",
                progress=100,
                step_name=STEP_PERSIST_EXCEL_STAGE2,
                message=(
                    "BH 和 BOX 左右进处理完成"
                    if stage_result.status == "complete"
                    else "项目无 BH/BOX，第二阶段已原样完成"
                    if stage_result.status == "noop"
                    else "BH 和 BOX 左右进处理已部分完成，请按红色提示人工处理"
                ),
                phase="completed",
                stage2_status=stage_result.status,
                reader_file_id=reader_result.result_file_id,
                stage2_file_id=formal_result.result_file_id,
                part_count=batch.part_count,
                component_count=batch.component_count,
            ),
        )
    except Stage2AttemptInactive:
        db.rollback()
    except Stage2WorkerError as exc:
        db.rollback()
        if "attempt" in locals():
            _mark_reader_result_failed(
                db,
                result_id=reader_result.id if reader_result is not None else None,
                error_code=exc.code,
            )
            _mark_reader_result_failed(
                db,
                result_id=(
                    box_reader_result.id
                    if box_reader_result is not None
                    else None
                ),
                error_code=exc.code,
            )
            _mark_failed(
                db,
                job_id=job_id,
                attempt=attempt,
                error_code=exc.code,
                error_message=str(exc),
            )
    except Exception as exc:
        db.rollback()
        logger.error(
            "Excel Stage2 worker failed for job %s (error_type=%s): %s",
            job_id,
            exc.__class__.__name__,
            str(exc)[-2000:],
        )
        if "attempt" in locals():
            _mark_reader_result_failed(
                db,
                result_id=reader_result.id if reader_result is not None else None,
                error_code="EXCEL_STAGE2_INTERNAL_ERROR",
            )
            _mark_reader_result_failed(
                db,
                result_id=(
                    box_reader_result.id
                    if box_reader_result is not None
                    else None
                ),
                error_code="EXCEL_STAGE2_INTERNAL_ERROR",
            )
            _mark_failed(
                db,
                job_id=job_id,
                attempt=attempt,
                error_code="EXCEL_STAGE2_INTERNAL_ERROR",
                error_message="Excel 第二阶段处理失败，请稍后重试或联系管理员。",
            )
    finally:
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)
        db.close()


__all__ = [
    "BhReaderArtifacts",
    "BoxReaderArtifacts",
    "ExcelStage2WorkerInputs",
    "Stage2WorkerError",
    "resolve_excel_stage2_worker_inputs",
    "run_bh_reader_batch",
    "run_box_reader_batch",
    "run_excel_stage2_processing",
    "stage_registered_file",
]
