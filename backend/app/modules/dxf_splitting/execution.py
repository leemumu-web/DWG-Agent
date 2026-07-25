"""Attempt-aware workflow orchestration for Steel DXF Split 1.5.2."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.dxf_classification.interface import (
    DxfSplitCandidateInput,
    latest_classification_run,
    list_split_candidate_inputs,
)
from app.modules.dxf_splitting.adapter import (
    BH_PROJECT_LEDGER_FILENAME,
    BH_SOURCE_CONTRACT,
    BOX_SOURCE_CONTRACT,
    CLASSIFIED_INPUT_SCHEMA,
    CLI_SCHEMA,
    MANIFEST_SCHEMA,
    MAX_AUTOMATIC_ATTEMPTS,
    SPLITTER_VERSION,
    VALIDATION_SCHEMA,
    DxfSplitError,
)
from app.modules.dxf_splitting.adapter import invoke_splitter as _invoke_splitter
from app.modules.dxf_splitting.adapter import (
    publish_empty_bh_ledger as _publish_empty_bh_ledger,
)
from app.modules.dxf_splitting.persistence import (
    finish_split_run,
    get_or_create_split_run,
    load_split_run,
    mark_split_failed,
    mark_split_interrupted,
    persist_split_output,
    record_split_analysis,
    record_split_item,
)
from app.modules.dxf_splitting.validation import (
    StagedSplitSource,
    ValidatedSplitItem,
    build_validation_report,
    validate_split_results,
)
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import (
    Job,
    JobStep,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    dispatch_committed_job,
    make_event,
    retry_job,
)
from app.modules.workflows.interface import (
    WorkflowRun,
    attach_artifact,
    bind_stage_job,
    read_verified_input_object,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_STEEL_DXF_SPLIT,
    STEP_PERSIST_STEEL_DXF_SPLIT,
    STEP_RUN_STEEL_DXF_SPLIT,
    STEP_VALIDATE_STEEL_DXF_SPLIT,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.database.session import SessionLocal

logger = logging.getLogger(__name__)


def _add_step(
    db: Session,
    job_id: int,
    attempt: int,
    name: str,
    worker_name: str,
    *,
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    db.add(
        JobStep(
            job_id=job_id,
            attempt=attempt,
            step_name=name,
            worker_name=worker_name,
            status="succeeded",
            input_json=input_json,
            output_json=output_json,
            started_at=now,
            finished_at=now,
        )
    )


def _portable_value(
    value: object,
    *,
    input_root: Path,
    output_root: Path,
) -> object:
    if isinstance(value, dict):
        return {
            str(key): _portable_value(
                item,
                input_root=input_root,
                output_root=output_root,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _portable_value(
                item,
                input_root=input_root,
                output_root=output_root,
            )
            for item in value
        ]
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/")
    output_prefix = output_root.resolve().as_posix()
    input_prefix = input_root.resolve().as_posix()
    if normalized.casefold().startswith((output_prefix + "/").casefold()):
        return f"split-output/{normalized[len(output_prefix) + 1 :]}"
    if normalized.casefold().startswith((input_prefix + "/").casefold()):
        return f"classified-input/{normalized[len(input_prefix) + 1 :]}"
    return value


def _portable_report(
    source: Path,
    destination: Path,
    *,
    input_root: Path,
    output_root: Path,
) -> Path:
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DxfSplitError(f"拆板报告不可读取: {source.name}") from exc
    portable = _portable_value(
        payload,
        input_root=input_root,
        output_root=output_root,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(portable, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return destination


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def _write_classification_manifest(
    path: Path,
    sources: list[StagedSplitSource],
) -> Path:
    items: list[dict[str, str]] = []
    for source in sources:
        family = source.semantic.part_type
        if family not in {"BH", "BOX"}:
            raise DxfSplitError(
                f"分类条目 {source.semantic.classification_item_id} "
                f"没有可直接拆板的 BH/BOX 类型。"
            )
        if Path(source.source_name).name != source.source_name:
            raise DxfSplitError(
                f"分类条目 {source.semantic.classification_item_id} 的文件名无效。"
            )
        items.append({"file_name": source.source_name, "family": family})
    return _write_json(
        path,
        {
            "schema": CLASSIFIED_INPUT_SCHEMA,
            "items": items,
        },
    )


def _load_workflow(db: Session, workflow_id: int) -> WorkflowRun | None:
    return db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == workflow_id)
        .options(
            selectinload(WorkflowRun.stages),
            selectinload(WorkflowRun.artifacts),
        )
    )


def _stage_sources(
    db: Session,
    inputs: list[DxfSplitCandidateInput],
    input_directory: Path,
) -> list[StagedSplitSource]:
    supported: list[StagedSplitSource] = []
    seen_names: set[str] = set()
    for semantic in inputs:
        stored = db.get(StoredFile, semantic.output_file_id)
        if stored is None or stored.status == "deleted" or stored.file_ext.casefold() != ".dxf":
            raise DxfSplitError(f"分类条目 {semantic.classification_item_id} 的 DXF 不可用。")
        source_name = Path(stored.original_name).name
        staged_path = input_directory / source_name
        source = StagedSplitSource(
            semantic=semantic,
            source_name=source_name,
            staged_path=staged_path,
        )
        payload = read_verified_input_object(stored)
        key = source_name.casefold()
        if key in seen_names:
            raise DxfSplitError(f"自动拆板输入文件名冲突: {source_name}")
        seen_names.add(key)
        staged_path.write_bytes(payload)
        supported.append(source)
    return supported


def _artifact_metadata(
    *,
    job: Job,
    run_id: int,
    split_item_id: int | None = None,
    classification_item_id: int | None = None,
    role: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": job.id,
        "job_attempt": job.attempt,
        "split_run_id": run_id,
        "role": role,
    }
    if split_item_id is not None:
        payload["split_item_id"] = split_item_id
    if classification_item_id is not None:
        payload["classification_item_id"] = classification_item_id
    return payload


def _persist_validated_item(
    db: Session,
    *,
    job: Job,
    workflow: WorkflowRun,
    run,
    validated: ValidatedSplitItem,
    input_root: Path,
    output_root: Path,
    normalized_report_root: Path,
    batch_name: str,
):
    normal_file = None
    allowance_file = None
    split_report_file = None
    allowance_report_file = None
    candidate_normal_file = None
    candidate_allowance_file = None
    candidate_split_report_file = None
    candidate_allowance_report_file = None
    classification_item_id = validated.source.semantic.classification_item_id
    prefix = f"items/{classification_item_id}"
    if validated.automation_route == "auto_accepted":
        required = (
            validated.normal_dxf_path,
            validated.weld_allowance_dxf_path,
            validated.split_report_path,
            validated.weld_allowance_report_path,
        )
        if any(path is None for path in required):
            raise DxfSplitError("自动验收条目缺少成对 DXF 或报告。")
        assert validated.normal_dxf_path is not None
        assert validated.weld_allowance_dxf_path is not None
        assert validated.split_report_path is not None
        assert validated.weld_allowance_report_path is not None
        normal_file = persist_split_output(
            db,
            job=job,
            workflow_id=workflow.id,
            attempt=job.attempt,
            relative_path=f"{prefix}/normal/{validated.normal_dxf_path.name}",
            path=validated.normal_dxf_path,
            batch_name=batch_name,
            content_type="application/dxf",
        )
        allowance_file = persist_split_output(
            db,
            job=job,
            workflow_id=workflow.id,
            attempt=job.attempt,
            relative_path=(f"{prefix}/weld-allowance/{validated.weld_allowance_dxf_path.name}"),
            path=validated.weld_allowance_dxf_path,
            batch_name=batch_name,
            content_type="application/dxf",
        )
        portable_split_report = _portable_report(
            validated.split_report_path,
            normalized_report_root / prefix / validated.split_report_path.name,
            input_root=input_root,
            output_root=output_root,
        )
        portable_allowance_report = _portable_report(
            validated.weld_allowance_report_path,
            normalized_report_root / prefix / validated.weld_allowance_report_path.name,
            input_root=input_root,
            output_root=output_root,
        )
        split_report_file = persist_split_output(
            db,
            job=job,
            workflow_id=workflow.id,
            attempt=job.attempt,
            relative_path=f"{prefix}/reports/{portable_split_report.name}",
            path=portable_split_report,
            batch_name=batch_name,
            content_type="application/json",
        )
        allowance_report_file = persist_split_output(
            db,
            job=job,
            workflow_id=workflow.id,
            attempt=job.attempt,
            relative_path=f"{prefix}/reports/{portable_allowance_report.name}",
            path=portable_allowance_report,
            batch_name=batch_name,
            content_type="application/json",
        )
    elif (
        validated.normal_dxf_path is not None
        and validated.weld_allowance_dxf_path is not None
    ):
        candidate_normal_file = persist_split_output(
            db,
            job=job,
            workflow_id=workflow.id,
            attempt=job.attempt,
            relative_path=f"{prefix}/candidates/{validated.normal_dxf_path.name}",
            path=validated.normal_dxf_path,
            batch_name=batch_name,
            content_type="application/dxf",
        )
        candidate_allowance_file = persist_split_output(
            db,
            job=job,
            workflow_id=workflow.id,
            attempt=job.attempt,
            relative_path=(
                f"{prefix}/candidates/{validated.weld_allowance_dxf_path.name}"
            ),
            path=validated.weld_allowance_dxf_path,
            batch_name=batch_name,
            content_type="application/dxf",
        )
        if validated.split_report_path is not None:
            candidate_split_report_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=job.attempt,
                relative_path=(
                    f"{prefix}/candidates/{validated.split_report_path.name}"
                ),
                path=validated.split_report_path,
                batch_name=batch_name,
                content_type="application/json",
            )
        if validated.weld_allowance_report_path is not None:
            candidate_allowance_report_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=job.attempt,
                relative_path=(
                    f"{prefix}/candidates/{validated.weld_allowance_report_path.name}"
                ),
                path=validated.weld_allowance_report_path,
                batch_name=batch_name,
                content_type="application/json",
            )
    item = record_split_item(
        db,
        run=run,
        validated=validated,
        normal_file=normal_file,
        allowance_file=allowance_file,
        split_report_file=split_report_file,
        allowance_report_file=allowance_report_file,
        candidate_normal_file=candidate_normal_file,
        candidate_allowance_file=candidate_allowance_file,
        candidate_split_report_file=candidate_split_report_file,
        candidate_allowance_report_file=candidate_allowance_report_file,
    )
    if normal_file is not None:
        attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=normal_file.id,
            metadata=_artifact_metadata(
                job=job,
                run_id=run.id,
                split_item_id=item.id,
                classification_item_id=classification_item_id,
                role="normal_dxf",
            ),
        )
    if allowance_file is not None:
        attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="weld_allowance_dxf",
            file_id=allowance_file.id,
            metadata=_artifact_metadata(
                job=job,
                run_id=run.id,
                split_item_id=item.id,
                classification_item_id=classification_item_id,
                role="weld_allowance_dxf",
            ),
        )
    for artifact_type, stored, role in (
        ("split_report", split_report_file, "split_report"),
        (
            "weld_allowance_report",
            allowance_report_file,
            "weld_allowance_report",
        ),
    ):
        if stored is not None:
            attach_artifact(
                db,
                workflow,
                stage_code="drawing_processing",
                artifact_type=artifact_type,
                file_id=stored.id,
                metadata=_artifact_metadata(
                    job=job,
                    run_id=run.id,
                    split_item_id=item.id,
                    classification_item_id=classification_item_id,
                    role=role,
                ),
            )
    return item


def _retry_failed_attempt(
    db: Session,
    *,
    job_id: int,
    workflow_id: int,
    failed_attempt: int,
) -> None:
    if failed_attempt >= MAX_AUTOMATIC_ATTEMPTS:
        return
    job = db.get(Job, job_id, populate_existing=True)
    workflow = _load_workflow(db, workflow_id)
    if job is None or workflow is None or job.status != "failed":
        return
    retried = retry_job(db, job)
    bind_stage_job(
        db,
        workflow,
        stage_code="drawing_processing",
        job=retried,
    )
    db.commit()
    try:
        dispatch_committed_job(db, retried)
    except Exception:
        logger.exception(
            "Failed to dispatch automatic DXF split retry for job %s attempt %s",
            job_id,
            retried.attempt,
        )
        _retry_failed_attempt(
            db,
            job_id=job_id,
            workflow_id=workflow_id,
            failed_attempt=retried.attempt,
        )


def run_dxf_splitting(
    job_id: int,
    *,
    worker_name: str = "celery_dxf_split",
    expected_attempt: int = 1,
) -> None:
    db = SessionLocal()
    workflow_id = 0
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_STEEL_DXF_SPLIT,
            progress=5,
            message="DXF 拆板任务已接收",
        )
        if job is None:
            return
        attempt = job.attempt
        workflow_id = int((job.params_json or {}).get("workflow_id") or 0)
        workflow = _load_workflow(db, workflow_id)
        drawing_stage = (
            next(
                (
                    stage
                    for stage in workflow.stages
                    if stage.stage_code == "drawing_processing"
                ),
                None,
            )
            if workflow is not None
            else None
        )
        if (
            workflow is None
            or workflow.current_stage != "drawing_processing"
            or job.project_id != workflow.project_id
            or drawing_stage is None
            or drawing_stage.job_id != job.id
            or drawing_stage.job_attempt != attempt
        ):
            raise DxfSplitError("Job 未绑定当前拆板阶段。")
        classification = latest_classification_run(db, workflow.id)
        if classification is None:
            raise DxfSplitError("工作流缺少 DXF 分类运行。")
        classification_job = db.get(Job, classification.job_id)
        requested_classification_run_id = int(
            (job.params_json or {}).get("classification_run_id") or 0
        )
        if (
            requested_classification_run_id != classification.id
            or classification.project_id != workflow.project_id
            or classification_job is None
            or classification_job.project_id != workflow.project_id
            or classification_job.task_type != TASK_STEEL_DXF_CLASSIFICATION
            or classification_job.status != "succeeded"
            or classification_job.attempt != classification.job_attempt
        ):
            raise DxfSplitError("拆板 Job 的分类 run/Job 账本不一致。")
        if classification.status not in {"completed", "completed_with_review"}:
            raise DxfSplitError("DXF 分类运行尚未形成可追溯输出。")
        manifest_sha256 = str((job.params_json or {}).get("input_manifest_sha256") or "")
        if not manifest_sha256 or manifest_sha256 != classification.input_manifest_sha256:
            raise DxfSplitError("拆板 Job 的冻结输入摘要与分类运行不一致。")
        inputs = list_split_candidate_inputs(db, workflow.id)
        if not inputs:
            raise DxfSplitError("最新分类运行没有可交给拆板的 DXF。")
        run = get_or_create_split_run(
            db,
            job=job,
            workflow=workflow,
            classification_run_id=classification.id,
            attempt=attempt,
            manifest_sha256=manifest_sha256,
            input_count=len(inputs),
        )

        with tempfile.TemporaryDirectory(prefix=f"dxf-split-{job.id}-{attempt}-") as raw_root:
            root = Path(raw_root)
            input_directory = root / "classified-input"
            output_directory = root / "split-output"
            normalized_report_root = root / "portable-reports"
            input_directory.mkdir()
            output_directory.mkdir()
            supported = _stage_sources(
                db,
                inputs,
                input_directory,
            )
            classification_manifest = _write_classification_manifest(
                root / "platform" / "classified-split-input.json",
                supported,
            )
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=25,
                event=make_event(
                    type_="progress",
                    status=JOB_RUNNING,
                    progress=25,
                    message="拆板输入已从 MinIO 校验并暂存",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return

            if supported:
                def publish_progress(
                    processed: int,
                    total: int,
                    auto_count: int = 0,
                    manual_count: int = 0,
                    failed_count: int = 0,
                ) -> None:
                    overall_processed = min(processed, len(inputs))
                    current_run = load_split_run(db, job_id=job.id, attempt=attempt)
                    current_run.processed_count = overall_processed
                    current_run.auto_accepted_count = auto_count
                    current_run.manual_review_count = (
                        manual_count + failed_count
                    )
                    current_run.failed_count = failed_count
                    db.flush()
                    progress = 25 + round(35 * overall_processed / len(inputs))
                    active_job = commit_job_progress(
                        db,
                        job.id,
                        attempt=attempt,
                        progress=progress,
                        event=make_event(
                            type_="progress",
                            status=JOB_RUNNING,
                            progress=progress,
                            message=(
                                f"已拆板 {overall_processed} / {len(inputs)} 张 DXF"
                            ),
                            processed_count=overall_processed,
                            input_count=len(inputs),
                            auto_accepted_count=auto_count,
                            manual_review_count=(
                                manual_count + failed_count
                            ),
                            failed_count=failed_count,
                        ),
                    )
                    if active_job is None:
                        raise DxfSplitError("拆板 Job attempt 已不再有效。")

                cli_payload = _invoke_splitter(
                    input_directory,
                    output_directory,
                    classification_manifest=classification_manifest,
                    expected_input_count=len(supported),
                    progress_callback=publish_progress,
                )
                ledger_path = output_directory / BH_PROJECT_LEDGER_FILENAME
            else:
                ledger_path = _publish_empty_bh_ledger(output_directory)
                cli_payload = {
                    "schema": CLI_SCHEMA,
                    "splitter_version": SPLITTER_VERSION,
                    "status": "completed",
                    "exit_code": 0,
                    "input_count": 0,
                    "auto_accepted_count": 0,
                    "manual_review_count": 0,
                    "results": [],
                }
            if not ledger_path.is_file():
                raise DxfSplitError("拆板批次没有生成 BH拆板信息表.xlsx。")
            _add_step(
                db,
                job.id,
                attempt,
                STEP_RUN_STEEL_DXF_SPLIT,
                worker_name,
                input_json={
                    "splitter_version": SPLITTER_VERSION,
                    "input_count": len(inputs),
                    "automatic_input_count": len(supported),
                    "classification_only_count": max(
                        classification.input_count - len(inputs),
                        0,
                    ),
                    "manifest_sha256": manifest_sha256,
                    "source_contracts": {
                        "BH": BH_SOURCE_CONTRACT,
                        "BOX": BOX_SOURCE_CONTRACT,
                    },
                },
                output_json={
                    "status": cli_payload["status"],
                    "auto_accepted_count": cli_payload["auto_accepted_count"],
                    "manual_review_count": cli_payload["manual_review_count"],
                },
            )
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=60,
                event=make_event(
                    type_="progress",
                    status=JOB_RUNNING,
                    progress=60,
                    message="整批 DXF 拆板执行完成",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return

            validated = validate_split_results(
                supported,
                cli_payload,
                output_directory,
            )
            validated.sort(key=lambda item: item.source.semantic.classification_item_id)
            validation_payload = build_validation_report(
                workflow_id=workflow.id,
                split_run_id=run.id,
                job_attempt=attempt,
                input_manifest_sha256=manifest_sha256,
                items=validated,
            )
            manual_count = int(validation_payload["manual_review_count"])
            auto_count = int(validation_payload["auto_accepted_count"])
            failed_count = int(validation_payload["failed_count"])
            _add_step(
                db,
                job.id,
                attempt,
                STEP_VALIDATE_STEEL_DXF_SPLIT,
                worker_name,
                input_json={"schema": VALIDATION_SCHEMA},
                output_json={
                    "status": validation_payload["status"],
                    "auto_accepted_count": auto_count,
                    "manual_review_count": manual_count,
                },
            )
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=75,
                event=make_event(
                    type_="progress",
                    status=JOB_RUNNING,
                    progress=75,
                    message="拆板结果独立校验完成",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return

            run = load_split_run(db, job_id=job.id, attempt=attempt)
            batch_name = f"workflow-{workflow.id}-split-attempt-{attempt}"
            persisted_items = [
                _persist_validated_item(
                    db,
                    job=job,
                    workflow=workflow,
                    run=run,
                    validated=item,
                    input_root=input_directory,
                    output_root=output_directory,
                    normalized_report_root=normalized_report_root,
                    batch_name=batch_name,
                )
                for item in validated
            ]
            ledger_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=f"batch/{ledger_path.name}",
                path=ledger_path,
                batch_name=batch_name,
                content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )
            validation_path = _write_json(
                root / "platform" / "dxf-split-validation.json",
                validation_payload,
            )
            validation_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=f"batch/{validation_path.name}",
                path=validation_path,
                batch_name=batch_name,
                content_type="application/json",
            )
            run_status = "completed_with_review" if manual_count else "completed"
            manifest_payload: dict[str, object] = {
                "schema": MANIFEST_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "job_id": job.id,
                "job_attempt": attempt,
                "status": run_status,
                "splitter_version": SPLITTER_VERSION,
                "cli_schema": CLI_SCHEMA,
                "validation_schema": VALIDATION_SCHEMA,
                "input_manifest_sha256": manifest_sha256,
                "source_contracts": run.source_contracts_json or {},
                "input_count": len(persisted_items),
                "auto_accepted_count": auto_count,
                "manual_review_count": manual_count,
                "failed_count": failed_count,
                "bh_split_ledger_file_id": ledger_file.id,
                "validation_report_file_id": validation_file.id,
                "items": [
                    {
                        "split_item_id": item.id,
                        "classification_item_id": item.classification_item_id,
                        "drawing_id": item.drawing_id,
                        "source_file_id": item.source_file_id,
                        "part_type": item.part_type,
                        "automation_route": item.automation_route,
                        "normal_dxf_file_id": item.normal_dxf_file_id,
                        "weld_allowance_dxf_file_id": (item.weld_allowance_dxf_file_id),
                        "split_report_file_id": item.split_report_file_id,
                        "weld_allowance_report_file_id": (item.weld_allowance_report_file_id),
                    }
                    for item in persisted_items
                ],
            }
            manifest_path = _write_json(
                root / "platform" / "dxf-split-manifest.json",
                manifest_payload,
            )
            manifest_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=f"batch/{manifest_path.name}",
                path=manifest_path,
                batch_name=batch_name,
                content_type="application/json",
            )
            finish_split_run(
                run,
                auto_accepted_count=auto_count,
                manual_review_count=manual_count,
                failed_count=failed_count,
                ledger_file=ledger_file,
                manifest_file=manifest_file,
                validation_file=validation_file,
            )
            analysis = record_split_analysis(
                db,
                job=job,
                workflow_id=workflow.id,
                run=run,
                manifest_file=manifest_file,
                validation_file=validation_file,
            )
            for artifact_type, stored, role, result_id in (
                ("bh_split_ledger", ledger_file, "bh_split_ledger", None),
                (
                    "validation_report",
                    validation_file,
                    "validation_report",
                    None,
                ),
                (
                    "split_manifest",
                    manifest_file,
                    "split_manifest",
                    analysis.id,
                ),
            ):
                attach_artifact(
                    db,
                    workflow,
                    stage_code="drawing_processing",
                    artifact_type=artifact_type,
                    file_id=stored.id,
                    result_id=result_id,
                    metadata=_artifact_metadata(
                        job=job,
                        run_id=run.id,
                        role=role,
                    ),
                )
            _add_step(
                db,
                job.id,
                attempt,
                STEP_PERSIST_STEEL_DXF_SPLIT,
                worker_name,
                output_json={
                    "run_id": run.id,
                    "status": run.status,
                    "ledger_file_id": ledger_file.id,
                    "manifest_file_id": manifest_file.id,
                    "validation_report_file_id": validation_file.id,
                },
            )
            completed_job = complete_job_attempt(
                db,
                job.id,
                attempt=attempt,
                event=make_event(
                    type_="done",
                    status=JOB_SUCCEEDED,
                    progress=100,
                    message=(
                        "DXF 拆板完成，存在待人工复核图纸"
                        if manual_count
                        else "DXF 拆板及独立校验全部完成"
                    ),
                    run_id=run.id,
                    split_status=run.status,
                    auto_accepted_count=auto_count,
                    manual_review_count=manual_count,
                ),
            )
            if completed_job is None:
                mark_split_interrupted(db, job_id, attempt)
    except Exception as exc:
        db.rollback()
        if "attempt" in locals():
            try:
                mark_split_failed(db, job_id, attempt, exc)
                if workflow_id:
                    _retry_failed_attempt(
                        db,
                        job_id=job_id,
                        workflow_id=workflow_id,
                        failed_attempt=attempt,
                    )
            except Exception:
                db.rollback()
                logger.exception("Failed to persist or retry split failure for job %s", job_id)
        logger.exception("DXF split failed for job %s", job_id)
    finally:
        db.close()
