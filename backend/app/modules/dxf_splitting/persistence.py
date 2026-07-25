"""Attempt-aware MySQL and MinIO persistence for DXF split processing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.dxf_splitting.adapter import (
    BH_SOURCE_CONTRACT,
    BOX_SOURCE_CONTRACT,
    CLI_SCHEMA,
    ERROR_CODE_SPLIT_FAILED,
    MANIFEST_SCHEMA,
    SPLITTER_VERSION,
    VALIDATION_SCHEMA,
    DxfSplitError,
    source_contract_for,
)
from app.modules.dxf_splitting.models import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.dxf_splitting.schemas import (
    DxfSplitExcelHandoff,
    DxfSplitHandoffDrawing,
)
from app.modules.dxf_splitting.validation import ValidatedSplitItem
from app.modules.files.interface import (
    StoredFile,
    complete_transfer_in_transaction,
    prepare_generated_file_transfer,
    save_bytes_as_file,
)
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    fail_job_attempt,
)
from app.modules.workflows.interface import WorkflowRun
from app.platform.config.constants import (
    JOB_PENDING,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_VALIDATING,
    JOB_WAITING_CAD_WORKER,
    TASK_STEEL_DXF_SPLIT,
)
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException


def split_request_id(*, job_id: int, attempt: int, relative_path: str) -> str:
    semantic_key = f"{job_id}:{attempt}:{relative_path}".encode()
    digest = hashlib.sha256(semantic_key).hexdigest()[:50]
    return f"dxf-split:{digest}"


def get_or_create_split_run(
    db: Session,
    *,
    job: Job,
    workflow: WorkflowRun,
    classification_run_id: int,
    attempt: int,
    manifest_sha256: str,
    input_count: int,
) -> DxfSplitRun:
    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.job_id == job.id,
            DxfSplitRun.job_attempt == attempt,
        )
    )
    if run is None:
        run = DxfSplitRun(
            workflow_run_id=workflow.id,
            project_id=workflow.project_id,
            classification_run_id=classification_run_id,
            job_id=job.id,
            job_attempt=attempt,
            status="running",
            splitter_version=SPLITTER_VERSION,
            input_manifest_sha256=manifest_sha256,
            input_count=input_count,
            source_contracts_json={
                "BH": BH_SOURCE_CONTRACT,
                "BOX": BOX_SOURCE_CONTRACT,
            },
            started_at=datetime.now(UTC),
        )
        db.add(run)
        db.commit()
    elif (
        run.workflow_run_id != workflow.id
        or run.project_id != workflow.project_id
        or run.classification_run_id != classification_run_id
        or run.input_manifest_sha256 != manifest_sha256
        or run.input_count != input_count
    ):
        raise DxfSplitError("已存在的拆板 attempt 与当前冻结输入不一致。")
    return run


def load_split_run(db: Session, *, job_id: int, attempt: int) -> DxfSplitRun:
    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.job_id == job_id,
            DxfSplitRun.job_attempt == attempt,
        )
    )
    if run is None:
        raise DxfSplitError("拆板运行账本不存在。")
    return run


def persist_split_output(
    db: Session,
    *,
    job: Job,
    workflow_id: int,
    attempt: int,
    relative_path: str,
    path: Path,
    batch_name: str,
    content_type: str,
) -> StoredFile:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DxfSplitError("拆板产物相对路径无效。")
    payload = path.read_bytes()
    bucket = (
        settings.minio_bucket_dxf_derived
        if path.suffix.casefold() == ".dxf"
        else settings.minio_bucket_reports
    )
    storage_key = (
        f"workflows/{workflow_id}/drawing-processing/attempt-{attempt}/{relative.as_posix()}"
    )
    transfer_uid = prepare_generated_file_transfer(
        db,
        actor_user_id=job.created_by,
        request_id=split_request_id(
            job_id=job.id,
            attempt=attempt,
            relative_path=relative.as_posix(),
        ),
        batch_ref=batch_name,
        bucket=bucket,
        storage_key=storage_key,
        original_name=path.name,
        expected_bytes=len(payload),
    )
    stored = save_bytes_as_file(
        db,
        bucket=bucket,
        storage_key=storage_key,
        original_name=path.name,
        file_ext=path.suffix.casefold(),
        content_type=content_type,
        payload=payload,
        uploaded_by=job.created_by,
        batch_name=batch_name,
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
    return stored


def record_split_item(
    db: Session,
    *,
    run: DxfSplitRun,
    validated: ValidatedSplitItem,
    normal_file: StoredFile | None = None,
    allowance_file: StoredFile | None = None,
    split_report_file: StoredFile | None = None,
    allowance_report_file: StoredFile | None = None,
    candidate_normal_file: StoredFile | None = None,
    candidate_allowance_file: StoredFile | None = None,
    candidate_split_report_file: StoredFile | None = None,
    candidate_allowance_report_file: StoredFile | None = None,
) -> DxfSplitItem:
    semantic = validated.source.semantic
    effective_part_type = (
        validated.family
        if validated.family in {"BH", "BOX"}
        else semantic.part_type or "UNKNOWN"
    )
    type_resolution = (
        "classifier_confirmed"
        if (
            validated.automation_route == "auto_accepted"
            and semantic.classification_disposition == "classified"
            and semantic.part_type == validated.family
        )
        else "splitter_detected"
        if validated.automation_route == "auto_accepted"
        and validated.family in {"BH", "BOX"}
        else "unresolved"
    )
    item = DxfSplitItem(
        run=run,
        classification_item_id=semantic.classification_item_id,
        drawing_id=semantic.drawing_id,
        source_file_id=semantic.output_file_id,
        source_name=validated.source.source_name,
        classification_disposition=semantic.classification_disposition,
        classification_part_type=semantic.part_type,
        type_resolution=type_resolution,
        part_type=effective_part_type,
        profile_normalized=semantic.profile_normalized,
        family=validated.family,
        source_contract_id=source_contract_for(effective_part_type),
        automation_route=validated.automation_route,
        disposition=validated.disposition,
        normal_dxf_file_id=normal_file.id if normal_file is not None else None,
        weld_allowance_dxf_file_id=(allowance_file.id if allowance_file is not None else None),
        split_report_file_id=(split_report_file.id if split_report_file is not None else None),
        weld_allowance_report_file_id=(
            allowance_report_file.id if allowance_report_file is not None else None
        ),
        candidate_normal_dxf_file_id=(
            candidate_normal_file.id if candidate_normal_file is not None else None
        ),
        candidate_weld_allowance_dxf_file_id=(
            candidate_allowance_file.id if candidate_allowance_file is not None else None
        ),
        candidate_split_report_file_id=(
            candidate_split_report_file.id
            if candidate_split_report_file is not None
            else None
        ),
        candidate_weld_allowance_report_file_id=(
            candidate_allowance_report_file.id
            if candidate_allowance_report_file is not None
            else None
        ),
        diagnostics_json=list(validated.diagnostics),
        validation_json=validated.validation,
    )
    db.add(item)
    db.flush()
    return item


def record_split_analysis(
    db: Session,
    *,
    job: Job,
    workflow_id: int,
    run: DxfSplitRun,
    manifest_file: StoredFile,
    validation_file: StoredFile,
) -> AnalysisResult:
    analysis = AnalysisResult(
        job_id=job.id,
        result_type=TASK_STEEL_DXF_SPLIT,
        result_json={
            "workflow_id": workflow_id,
            "run_id": run.id,
            "job_attempt": job.attempt,
            "workflow_artifact_type": "split_manifest",
            "status": run.status,
            "input_count": run.input_count,
            "auto_accepted_count": run.auto_accepted_count,
            "manual_review_count": run.manual_review_count,
            "validation_report_file_id": validation_file.id,
        },
        confidence=Decimal("1.0000"),
        result_file_id=manifest_file.id,
        algorithm_version=SPLITTER_VERSION,
        tool_version="steel-dxf-split",
        status="succeeded",
    )
    db.add(analysis)
    db.flush()
    return analysis


def finish_split_run(
    run: DxfSplitRun,
    *,
    auto_accepted_count: int,
    manual_review_count: int,
    failed_count: int,
    ledger_file: StoredFile,
    manifest_file: StoredFile,
    validation_file: StoredFile,
) -> None:
    run.status = "completed_with_review" if manual_review_count else "completed"
    run.cli_schema = CLI_SCHEMA
    run.validation_schema = VALIDATION_SCHEMA
    run.auto_accepted_count = auto_accepted_count
    run.manual_review_count = manual_review_count
    run.failed_count = failed_count
    run.processed_count = run.input_count
    run.bh_split_ledger_file_id = ledger_file.id
    run.split_manifest_file_id = manifest_file.id
    run.validation_report_file_id = validation_file.id
    run.error_code = None
    run.error_message = None
    run.finished_at = datetime.now(UTC)


def mark_split_failed(db: Session, job_id: int, attempt: int, exc: Exception) -> None:
    if isinstance(exc, DxfSplitError):
        message = str(exc) or exc.__class__.__name__
    else:
        message = exc.__class__.__name__
    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.job_id == job_id,
            DxfSplitRun.job_attempt == attempt,
        )
    )
    if run is not None:
        run.status = "failed"
        run.error_code = ERROR_CODE_SPLIT_FAILED
        run.error_message = message
        run.finished_at = datetime.now(UTC)
    fail_job_attempt(
        db,
        job_id,
        attempt=attempt,
        error_code=ERROR_CODE_SPLIT_FAILED,
        error_message=message,
    )


def reconcile_split_run_for_terminal_job(
    db: Session,
    *,
    job_id: int,
    attempt: int,
) -> bool:
    """Close an orphan run only after its exact Job attempt stopped being active."""

    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.job_id == job_id,
            DxfSplitRun.job_attempt == attempt,
        )
    )
    if run is None or run.status != "running":
        return False
    job = db.get(Job, job_id)
    active_statuses = {
        JOB_PENDING,
        JOB_QUEUED,
        JOB_RUNNING,
        JOB_VALIDATING,
        JOB_WAITING_CAD_WORKER,
    }
    if (
        job is not None
        and job.attempt == attempt
        and job.status in active_statuses
    ):
        return False
    run.status = "failed"
    run.error_code = "DXF_SPLIT_ATTEMPT_INTERRUPTED"
    run.error_message = "拆板 attempt 已被取消或由新的 attempt 取代。"
    run.finished_at = datetime.now(UTC)
    db.flush()
    return True


def reconcile_orphan_split_runs(db: Session) -> int:
    """Close every running split projection whose owning Job attempt is inactive."""

    active_statuses = (
        JOB_PENDING,
        JOB_QUEUED,
        JOB_RUNNING,
        JOB_VALIDATING,
        JOB_WAITING_CAD_WORKER,
    )
    candidates = db.execute(
        select(DxfSplitRun.job_id, DxfSplitRun.job_attempt)
        .join(Job, Job.id == DxfSplitRun.job_id)
        .where(
            DxfSplitRun.status == "running",
            or_(
                Job.attempt != DxfSplitRun.job_attempt,
                Job.status.not_in(active_statuses),
            ),
        )
    ).all()
    reconciled = 0
    for job_id, attempt in candidates:
        reconciled += int(
            reconcile_split_run_for_terminal_job(
                db,
                job_id=job_id,
                attempt=attempt,
            )
        )
    return reconciled


def mark_split_interrupted(db: Session, job_id: int, attempt: int) -> None:
    """Close one run after execution observes that its Job attempt is inactive."""

    if reconcile_split_run_for_terminal_job(
        db,
        job_id=job_id,
        attempt=attempt,
    ):
        db.commit()


def latest_split_run(db: Session, workflow_id: int) -> DxfSplitRun | None:
    return db.scalar(
        select(DxfSplitRun)
        .where(DxfSplitRun.workflow_run_id == workflow_id)
        .options(selectinload(DxfSplitRun.items))
        .order_by(DxfSplitRun.job_attempt.desc(), DxfSplitRun.id.desc())
    )


def get_split_outcome(db: Session, *, job_id: int, attempt: int) -> str | None:
    return db.scalar(
        select(DxfSplitRun.status).where(
            DxfSplitRun.job_id == job_id,
            DxfSplitRun.job_attempt == attempt,
        )
    )


def manual_review_archive_members(
    db: Session,
    run: DxfSplitRun,
) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    seen: set[str] = set()
    for item in run.items:
        if item.automation_route != "manual_review":
            continue
        stored = db.get(StoredFile, item.source_file_id)
        if stored is None or stored.status == "deleted" or stored.file_ext.casefold() != ".dxf":
            raise AppHTTPException(
                409,
                "DXF_SPLIT_REVIEW_SOURCE_MISSING",
                "待人工处理的原始 DXF 不可用。",
                {"split_item_id": item.id, "file_id": item.source_file_id},
            )
        name = Path(stored.original_name).name
        relative = name
        if relative.casefold() in seen:
            relative = f"{item.id}-{name}"
        seen.add(relative.casefold())
        members.append((stored.id, relative))
    return members


def split_candidate_files(
    db: Session,
    item: DxfSplitItem,
) -> tuple[StoredFile, StoredFile, StoredFile, StoredFile] | None:
    files = tuple(
        db.get(StoredFile, file_id) if file_id is not None else None
        for file_id in (
            item.candidate_normal_dxf_file_id,
            item.candidate_weld_allowance_dxf_file_id,
            item.candidate_split_report_file_id,
            item.candidate_weld_allowance_report_file_id,
        )
    )
    if any(stored is None or stored.status == "deleted" for stored in files):
        return None
    normal, allowance, split_report, allowance_report = files
    if (
        normal.file_ext.casefold() != ".dxf"
        or allowance.file_ext.casefold() != ".dxf"
        or normal.id == allowance.id
        or split_report.file_ext.casefold() != ".json"
        or allowance_report.file_ext.casefold() != ".json"
    ):
        return None
    return normal, allowance, split_report, allowance_report


def review_candidate_archive_members(
    db: Session,
    run: DxfSplitRun,
) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    for item in run.items:
        if item.automation_route != "manual_review":
            continue
        source = db.get(StoredFile, item.source_file_id)
        if source is None or source.status == "deleted":
            raise AppHTTPException(
                409,
                "DXF_SPLIT_REVIEW_SOURCE_MISSING",
                "待人工处理的原始 DXF 不可用。",
                {"split_item_id": item.id, "file_id": item.source_file_id},
            )
        files: list[tuple[StoredFile, str]] = [(source, "source")]
        candidates = split_candidate_files(db, item)
        if candidates is not None:
            normal, allowance, split_report, allowance_report = candidates
            files.extend(
                (
                    (normal, "candidate"),
                    (allowance, "candidate"),
                    (split_report, "reports"),
                    (allowance_report, "reports"),
                )
            )
        for stored, directory in files:
            members.append(
                (
                    stored.id,
                    f"items/{item.id}/{directory}/{Path(stored.original_name).name}",
                )
            )
    return members


def split_results_archive_members(
    db: Session,
    run: DxfSplitRun,
) -> list[tuple[int, str]]:
    if run.status not in {"completed", "completed_with_review"}:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_RESULTS_PENDING",
            "拆板批次尚未形成可交付的正式结果。",
            {"split_run_id": run.id, "status": run.status},
        )
    members: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    for item in run.items:
        if (
            run.status == "completed_with_review"
            and item.automation_route != "auto_accepted"
        ):
            continue
        for file_id, directory in (
            (item.normal_dxf_file_id, "原长"),
            (item.weld_allowance_dxf_file_id, "余量增长后短文件"),
        ):
            stored = db.get(StoredFile, file_id) if file_id is not None else None
            if (
                stored is None
                or stored.status == "deleted"
                or stored.file_ext.casefold() != ".dxf"
            ):
                raise AppHTTPException(
                    409,
                    "DXF_SPLIT_RESULT_FILE_MISSING",
                    "已通过拆板校验的正式 DXF 不可用。",
                    {"split_item_id": item.id, "file_id": file_id},
                )
            relative_path = f"{directory}/{Path(stored.original_name).name}"
            normalized_path = relative_path.casefold()
            if normalized_path in seen_paths:
                raise AppHTTPException(
                    409,
                    "DXF_SPLIT_RESULT_NAME_CONFLICT",
                    "正式拆板结果中存在同目录同名 DXF，无法安全生成 ZIP。",
                    {
                        "split_run_id": run.id,
                        "split_item_id": item.id,
                        "archive_path": relative_path,
                    },
                )
            seen_paths.add(normalized_path)
            members.append((stored.id, relative_path))
    if not members:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_RESULTS_EMPTY",
            "本批次没有通过校验、可供下载的正式拆板 DXF。",
            {
                "split_run_id": run.id,
                "status": run.status,
                "auto_accepted_count": run.auto_accepted_count,
            },
        )
    return members


def find_split_file_workflow_id(db: Session, file_id: int) -> int | None:
    """Resolve every split-owned file to its production ZIP download boundary."""
    item_workflow_id = db.scalar(
        select(DxfSplitRun.workflow_run_id)
        .join(DxfSplitItem, DxfSplitItem.run_id == DxfSplitRun.id)
        .outerjoin(
            DxfSplitReviewDecision,
            DxfSplitReviewDecision.split_item_id == DxfSplitItem.id,
        )
        .where(
            or_(
                DxfSplitItem.source_file_id == file_id,
                DxfSplitItem.normal_dxf_file_id == file_id,
                DxfSplitItem.weld_allowance_dxf_file_id == file_id,
                DxfSplitItem.split_report_file_id == file_id,
                DxfSplitItem.weld_allowance_report_file_id == file_id,
                DxfSplitItem.candidate_normal_dxf_file_id == file_id,
                DxfSplitItem.candidate_weld_allowance_dxf_file_id == file_id,
                DxfSplitItem.candidate_split_report_file_id == file_id,
                DxfSplitItem.candidate_weld_allowance_report_file_id == file_id,
                DxfSplitReviewDecision.final_normal_dxf_file_id == file_id,
                DxfSplitReviewDecision.final_weld_allowance_dxf_file_id == file_id,
            )
        )
        .limit(1)
    )
    if item_workflow_id is not None:
        return item_workflow_id
    return db.scalar(
        select(DxfSplitRun.workflow_run_id)
        .where(
            or_(
                DxfSplitRun.bh_split_ledger_file_id == file_id,
                DxfSplitRun.split_manifest_file_id == file_id,
                DxfSplitRun.validation_report_file_id == file_id,
            )
        )
        .limit(1)
    )


def split_file_reference_exists(file_id):
    """Return a correlated predicate covering every file owned by a split run."""
    def indexed_reference_exists(model, column):
        return (
            select(1)
            .select_from(model)
            .where(column == file_id)
            .correlate_except(model)
            .exists()
        )

    item_columns = (
        DxfSplitItem.source_file_id,
        DxfSplitItem.normal_dxf_file_id,
        DxfSplitItem.weld_allowance_dxf_file_id,
        DxfSplitItem.split_report_file_id,
        DxfSplitItem.weld_allowance_report_file_id,
        DxfSplitItem.candidate_normal_dxf_file_id,
        DxfSplitItem.candidate_weld_allowance_dxf_file_id,
        DxfSplitItem.candidate_split_report_file_id,
        DxfSplitItem.candidate_weld_allowance_report_file_id,
    )
    review_columns = (
        DxfSplitReviewDecision.final_normal_dxf_file_id,
        DxfSplitReviewDecision.final_weld_allowance_dxf_file_id,
    )
    run_columns = (
        DxfSplitRun.bh_split_ledger_file_id,
        DxfSplitRun.split_manifest_file_id,
        DxfSplitRun.validation_report_file_id,
    )
    return or_(
        *(
            indexed_reference_exists(DxfSplitItem, column)
            for column in item_columns
        ),
        *(
            indexed_reference_exists(DxfSplitReviewDecision, column)
            for column in review_columns
        ),
        *(
            indexed_reference_exists(DxfSplitRun, column)
            for column in run_columns
        ),
    )


def persist_review_completion_manifest(
    db: Session,
    *,
    run: DxfSplitRun,
    actor_user_id: int,
) -> StoredFile:
    relative_path = "batch/dxf-split-final-manifest.json"
    payload = json.dumps(
        {
            "schema": MANIFEST_SCHEMA,
            "workflow_id": run.workflow_run_id,
            "split_run_id": run.id,
            "job_id": run.job_id,
            "job_attempt": run.job_attempt,
            "status": "completed",
            "splitter_version": run.splitter_version,
            "input_manifest_sha256": run.input_manifest_sha256,
            "input_count": run.input_count,
            "machine_outcome": {
                "auto_accepted_count": run.auto_accepted_count,
                "manual_review_count": run.manual_review_count,
                "failed_count": run.failed_count,
            },
            "reviewed_count": sum(
                item.review_decision is not None
                for item in run.items
                if item.automation_route == "manual_review"
            ),
            "items": [
                {
                    "split_item_id": item.id,
                    "classification_item_id": item.classification_item_id,
                    "source_file_id": item.source_file_id,
                    "part_type": item.part_type,
                    "machine_automation_route": item.automation_route,
                    "machine_disposition": item.disposition,
                    "review_decision": (
                        item.review_decision.decision
                        if item.review_decision is not None
                        else None
                    ),
                    "review_decision_id": (
                        item.review_decision.id
                        if item.review_decision is not None
                        else None
                    ),
                    "normal_dxf_file_id": item.normal_dxf_file_id,
                    "weld_allowance_dxf_file_id": item.weld_allowance_dxf_file_id,
                    "split_report_file_id": item.split_report_file_id,
                    "weld_allowance_report_file_id": (
                        item.weld_allowance_report_file_id
                    ),
                }
                for item in run.items
            ],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    batch_name = (
        f"workflow-{run.workflow_run_id}-split-attempt-{run.job_attempt}"
    )
    transfer_uid = prepare_generated_file_transfer(
        db,
        actor_user_id=actor_user_id,
        request_id=split_request_id(
            job_id=run.job_id,
            attempt=run.job_attempt,
            relative_path=relative_path,
        ),
        batch_ref=batch_name,
        bucket=settings.minio_bucket_reports,
        storage_key=(
            f"workflows/{run.workflow_run_id}/drawing-processing/"
            f"attempt-{run.job_attempt}/{relative_path}"
        ),
        original_name="dxf-split-final-manifest.json",
        expected_bytes=len(payload),
    )
    stored = save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_reports,
        storage_key=(
            f"workflows/{run.workflow_run_id}/drawing-processing/"
            f"attempt-{run.job_attempt}/{relative_path}"
        ),
        original_name="dxf-split-final-manifest.json",
        file_ext=".json",
        content_type="application/json",
        payload=payload,
        uploaded_by=actor_user_id,
        batch_name=batch_name,
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
    return stored


def get_excel_split_handoff(
    db: Session,
    workflow_id: int,
) -> DxfSplitExcelHandoff:
    run = latest_split_run(db, workflow_id)
    if run is None:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_RUN_REQUIRED",
            "Excel 处理前必须存在已完成的 DXF 拆板批次。",
        )
    workflow = db.get(WorkflowRun, workflow_id)
    drawing_stage = (
        next(
            (stage for stage in workflow.stages if stage.stage_code == "drawing_processing"),
            None,
        )
        if workflow is not None
        else None
    )
    if (
        drawing_stage is None
        or drawing_stage.job_id != run.job_id
        or drawing_stage.job_attempt != run.job_attempt
    ):
        raise AppHTTPException(
            409,
            "DXF_SPLIT_RUN_STALE",
            "最新拆板批次不是工作流当前登记的正式 attempt。",
            {
                "split_run_id": run.id,
                "run_job_id": run.job_id,
                "run_job_attempt": run.job_attempt,
            },
        )
    if run.status not in {"completed", "completed_with_review"}:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_RESULTS_PENDING",
            "拆板批次尚未形成可交接的正式结果。",
            {"split_run_id": run.id, "status": run.status},
        )
    if run.bh_split_ledger_file_id is None:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_LEDGER_MISSING",
            "已完成的拆板批次缺少 BH拆板信息表.xlsx。",
        )
    drawings: list[DxfSplitHandoffDrawing] = []
    required_file_ids = [run.bh_split_ledger_file_id]
    for item in run.items:
        if item.automation_route != "auto_accepted":
            continue
        if (
            item.normal_dxf_file_id is None
            or item.weld_allowance_dxf_file_id is None
        ):
            raise AppHTTPException(
                409,
                "DXF_SPLIT_HANDOFF_INCOMPLETE",
                "拆板批次无法形成完整的 Excel 交接数据。",
                {"split_item_id": item.id},
            )
        required_file_ids.extend([item.normal_dxf_file_id, item.weld_allowance_dxf_file_id])
        drawings.append(
            DxfSplitHandoffDrawing(
                drawing_id=item.drawing_id,
                classification_item_id=item.classification_item_id,
                source_file_id=item.source_file_id,
                normal_dxf_file_id=item.normal_dxf_file_id,
                weld_allowance_dxf_file_id=item.weld_allowance_dxf_file_id,
                part_type=item.part_type,
            )
        )
    if not drawings or len(drawings) != run.auto_accepted_count:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_HANDOFF_INCOMPLETE",
            "拆板批次的正式配对结果数量与 Excel 交接账本不一致。",
            {
                "split_run_id": run.id,
                "expected_count": run.auto_accepted_count,
                "handoff_count": len(drawings),
            },
        )
    unavailable = [
        file_id
        for file_id in required_file_ids
        if ((stored := db.get(StoredFile, file_id)) is None or stored.status == "deleted")
    ]
    if unavailable:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_HANDOFF_FILE_MISSING",
            "已登记的拆板交接文件不可用。",
            {"file_ids": unavailable},
        )
    return DxfSplitExcelHandoff(
        workflow_id=workflow_id,
        split_run_id=run.id,
        job_attempt=run.job_attempt,
        input_manifest_sha256=run.input_manifest_sha256,
        bh_split_ledger_file_id=run.bh_split_ledger_file_id,
        drawings=drawings,
    )


__all__ = [
    "MANIFEST_SCHEMA",
    "finish_split_run",
    "find_split_file_workflow_id",
    "get_excel_split_handoff",
    "get_or_create_split_run",
    "get_split_outcome",
    "latest_split_run",
    "load_split_run",
    "manual_review_archive_members",
    "review_candidate_archive_members",
    "split_candidate_files",
    "split_results_archive_members",
    "mark_split_failed",
    "mark_split_interrupted",
    "persist_split_output",
    "persist_review_completion_manifest",
    "record_split_analysis",
    "record_split_item",
    "reconcile_orphan_split_runs",
    "reconcile_split_run_for_terminal_job",
]
