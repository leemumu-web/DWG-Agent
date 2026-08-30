"""Attempt-aware orchestration for the standalone PL splitting Stage."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.dxf_classification.interface import (
    DxfSplitCandidateInput,
    latest_classification_run,
    list_pl_split_candidate_inputs,
)
from app.modules.dxf_splitting.persistence import (
    finish_pl_split_run,
    get_or_create_pl_split_run,
    load_split_run,
    mark_pl_split_failed,
    mark_split_interrupted,
    persist_split_output,
    record_pl_split_analysis,
    record_pl_split_item,
)
from app.modules.dxf_splitting.pl_adapter import (
    PL_REPORT_SCHEMA,
    PL_SOURCE_CONTRACT_ID,
    PL_SPLITTER_VERSION,
    PlSplitError,
    invoke_pl_splitter,
)
from app.modules.dxf_splitting.pl_validation import validate_pl_result
from app.modules.dxf_splitting.validation import StagedSplitSource
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import (
    Job,
    JobStep,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    make_event,
)
from app.modules.workflows.interface import (
    WorkflowRun,
    attach_artifact,
    read_verified_input_object,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_PL_DXF_SPLIT,
    STEP_PERSIST_PL_DXF_SPLIT,
    STEP_RUN_PL_DXF_SPLIT,
    STEP_VALIDATE_PL_DXF_SPLIT,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.time import business_now

logger = logging.getLogger(__name__)

PL_VALIDATION_SCHEMA = "DWG-AGENT-PL-SPLIT-VALIDATION-1.0"
PL_MANIFEST_SCHEMA = "DWG-AGENT-PL-SPLIT-MANIFEST-1.0"
PL_LEDGER_SCHEMA = "DWG-AGENT-PL-SPLIT-LEDGER-1.0"


def _temporary_directory(job_id: int, attempt: int):
    work_root = Path(settings.dxf_split_work_root)
    try:
        work_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlSplitError("服务器 PL 拆板工作空间不可用。") from exc
    return tempfile.TemporaryDirectory(
        prefix=f"pl-split-{job_id}-{attempt}-",
        dir=work_root,
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


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


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
    now = business_now()
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


def _stage_sources(
    db: Session,
    inputs: list[DxfSplitCandidateInput],
    input_directory: Path,
) -> list[StagedSplitSource]:
    staged: list[StagedSplitSource] = []
    seen_names: set[str] = set()
    for semantic in inputs:
        if semantic.part_type != "PL":
            raise PlSplitError("PL Stage 收到了非 PL 分类条目。")
        stored = db.get(StoredFile, semantic.output_file_id)
        if stored is None or stored.status == "deleted" or stored.file_ext.casefold() != ".dxf":
            raise PlSplitError(f"PL 分类条目 {semantic.classification_item_id} 的 DXF 不可用。")
        source_name = Path(stored.original_name).name
        if source_name.casefold() in seen_names:
            raise PlSplitError(f"PL 拆板输入文件名冲突：{source_name}")
        seen_names.add(source_name.casefold())
        staged_path = input_directory / source_name
        staged_path.write_bytes(read_verified_input_object(stored))
        staged.append(
            StagedSplitSource(
                semantic=semantic,
                source_name=source_name,
                staged_path=staged_path,
            )
        )
    return staged


def _result_by_source(
    staged: list[StagedSplitSource],
    payload: dict[str, Any],
) -> list[tuple[StagedSplitSource, dict[str, Any]]]:
    expected = {source.staged_path.resolve(): source for source in staged}
    mapped: dict[Path, dict[str, Any]] = {}
    for value in payload.get("items", []):
        if not isinstance(value, dict) or not isinstance(value.get("source"), str):
            raise PlSplitError("PL 拆板报告含无效逐图记录。")
        source_path = Path(value["source"]).resolve()
        if source_path not in expected or source_path in mapped:
            raise PlSplitError("PL 拆板报告与冻结输入不是一一对应。")
        mapped[source_path] = value
    if set(mapped) != set(expected):
        raise PlSplitError("PL 拆板报告遗漏冻结输入。")
    return [(source, mapped[source.staged_path.resolve()]) for source in staged]


def _artifact_metadata(
    *,
    job: Job,
    run_id: int,
    role: str,
    split_item_id: int | None = None,
    classification_item_id: int | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "job_id": job.id,
        "job_attempt": job.attempt,
        "split_run_id": run_id,
        "family": "PL",
        "role": role,
    }
    if split_item_id is not None:
        metadata["split_item_id"] = split_item_id
    if classification_item_id is not None:
        metadata["classification_item_id"] = classification_item_id
    return metadata


def run_pl_dxf_splitting(
    job_id: int,
    *,
    worker_name: str = "celery_pl_dxf_split",
    expected_attempt: int = 1,
) -> None:
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_PL_DXF_SPLIT,
            progress=5,
            message="PL 拆板任务已接收",
        )
        if job is None:
            return
        attempt = job.attempt
        workflow_id = int((job.params_json or {}).get("workflow_id") or 0)
        workflow = _load_workflow(db, workflow_id)
        stage = (
            next(
                (item for item in workflow.stages if item.stage_code == "pl_xbox_split"),
                None,
            )
            if workflow is not None
            else None
        )
        if (
            workflow is None
            or workflow.current_stage != "pl_xbox_split"
            or job.project_id != workflow.project_id
            or stage is None
            or stage.job_id != job.id
            or stage.job_attempt != attempt
        ):
            raise PlSplitError("Job 未绑定当前 PL 拆板阶段。")
        classification = latest_classification_run(db, workflow.id)
        if classification is None:
            raise PlSplitError("工作流缺少 DXF 分类运行。")
        classification_job = db.get(Job, classification.job_id)
        requested_run_id = int((job.params_json or {}).get("classification_run_id") or 0)
        if (
            requested_run_id != classification.id
            or classification.project_id != workflow.project_id
            or classification_job is None
            or classification_job.task_type != TASK_STEEL_DXF_CLASSIFICATION
            or classification_job.status != "succeeded"
            or classification_job.attempt != classification.job_attempt
            or classification.status not in {"completed", "completed_with_review"}
        ):
            raise PlSplitError("PL 拆板 Job 的分类账本不一致。")
        manifest_sha256 = str((job.params_json or {}).get("input_manifest_sha256") or "")
        if not manifest_sha256 or manifest_sha256 != classification.input_manifest_sha256:
            raise PlSplitError("PL 拆板 Job 的冻结输入摘要不一致。")
        inputs = list_pl_split_candidate_inputs(db, workflow.id)
        if not inputs:
            raise PlSplitError("最新分类运行没有 PL 拆板候选。")
        run = get_or_create_pl_split_run(
            db,
            job=job,
            workflow=workflow,
            classification_run_id=classification.id,
            attempt=attempt,
            manifest_sha256=manifest_sha256,
            input_count=len(inputs),
        )

        with _temporary_directory(job.id, attempt) as raw_root:
            root = Path(raw_root)
            input_directory = root / "classified-pl-input"
            output_directory = root / "pl-split-output"
            platform_directory = root / "platform"
            input_directory.mkdir()
            output_directory.mkdir()
            staged = _stage_sources(db, inputs, input_directory)
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=25,
                event=make_event(
                    type_="progress",
                    status=JOB_RUNNING,
                    progress=25,
                    message="PL 输入已校验并暂存",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return
            stage_result = invoke_pl_splitter(
                input_directory,
                output_directory,
                timeout_seconds=settings.dxf_split_timeout_seconds,
            )
            _add_step(
                db,
                job.id,
                attempt,
                STEP_RUN_PL_DXF_SPLIT,
                worker_name,
                input_json={
                    "splitter_version": PL_SPLITTER_VERSION,
                    "input_count": len(staged),
                    "source_contract": PL_SOURCE_CONTRACT_ID,
                },
                output_json={
                    "exit_code": stage_result.exit_code,
                    "success_count": stage_result.payload["success_count"],
                    "rejected_count": stage_result.payload["rejected_count"],
                },
            )
            matched = _result_by_source(staged, stage_result.payload)
            validated = [
                validate_pl_result(source, report_item, output_directory)
                for source, report_item in matched
            ]
            auto_count = sum(item.automation_route == "auto_accepted" for item in validated)
            manual_count = len(validated) - auto_count
            failed_count = manual_count
            _add_step(
                db,
                job.id,
                attempt,
                STEP_VALIDATE_PL_DXF_SPLIT,
                worker_name,
                input_json={"schema": PL_VALIDATION_SCHEMA},
                output_json={
                    "auto_accepted_count": auto_count,
                    "manual_review_count": manual_count,
                },
            )
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=70,
                event=make_event(
                    type_="progress",
                    status=JOB_RUNNING,
                    progress=70,
                    message="PL 保存后独立校验完成",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return

            run = load_split_run(db, job_id=job.id, attempt=attempt)
            batch_name = f"workflow-{workflow.id}-pl-split-attempt-{attempt}"
            persisted_items = []
            validation_items: list[dict[str, object]] = []
            for index, ((source, report_item), validation) in enumerate(
                zip(matched, validated, strict=True),
                start=1,
            ):
                portable_item = dict(report_item)
                portable_item["source"] = source.source_name
                output = portable_item.get("output")
                if isinstance(output, dict):
                    portable_item["output"] = {
                        **output,
                        "path": Path(str(output.get("path"))).name,
                    }
                item_report_path = _write_json(
                    platform_directory / "items" / f"{index:04d}.json",
                    portable_item,
                )
                item_report_file = persist_split_output(
                    db,
                    job=job,
                    workflow_id=workflow.id,
                    attempt=attempt,
                    relative_path=f"reports/items/{item_report_path.name}",
                    path=item_report_path,
                    batch_name=batch_name,
                    content_type="application/json",
                    storage_stage="pl-xbox-split",
                )
                normal_file = None
                if (
                    validation.automation_route == "auto_accepted"
                    and validation.normal_dxf_path is not None
                ):
                    normal_file = persist_split_output(
                        db,
                        job=job,
                        workflow_id=workflow.id,
                        attempt=attempt,
                        relative_path=f"normal/{validation.normal_dxf_path.name}",
                        path=validation.normal_dxf_path,
                        batch_name=batch_name,
                        content_type="application/dxf",
                        storage_stage="pl-xbox-split",
                    )
                item = record_pl_split_item(
                    db,
                    run=run,
                    validated=validation,
                    normal_file=normal_file,
                    item_report_file=item_report_file,
                )
                persisted_items.append(item)
                validation_items.append(
                    {
                        "split_item_id": item.id,
                        "classification_item_id": item.classification_item_id,
                        "source_name": item.source_name,
                        "automation_route": item.automation_route,
                        "disposition": item.disposition,
                        "diagnostics": item.diagnostics_json or [],
                        "validation": item.validation_json or {},
                    }
                )
                if normal_file is not None:
                    attach_artifact(
                        db,
                        workflow,
                        stage_code="pl_xbox_split",
                        artifact_type="processed_dxf",
                        file_id=normal_file.id,
                        metadata=_artifact_metadata(
                            job=job,
                            run_id=run.id,
                            split_item_id=item.id,
                            classification_item_id=item.classification_item_id,
                            role="normal_dxf",
                        ),
                    )
                attach_artifact(
                    db,
                    workflow,
                    stage_code="pl_xbox_split",
                    artifact_type="split_report",
                    file_id=item_report_file.id,
                    metadata=_artifact_metadata(
                        job=job,
                        run_id=run.id,
                        split_item_id=item.id,
                        classification_item_id=item.classification_item_id,
                        role="item_report",
                    ),
                )

            validation_payload: dict[str, object] = {
                "schema": PL_VALIDATION_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "job_attempt": attempt,
                "input_manifest_sha256": manifest_sha256,
                "input_count": len(validated),
                "auto_accepted_count": auto_count,
                "manual_review_count": manual_count,
                "failed_count": failed_count,
                "items": validation_items,
            }
            validation_path = _write_json(
                platform_directory / "pl-split-validation.json",
                validation_payload,
            )
            ledger_payload: dict[str, object] = {
                "schema": PL_LEDGER_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "source_contract": PL_SOURCE_CONTRACT_ID,
                "items": [
                    {
                        "split_item_id": item.id,
                        "classification_item_id": item.classification_item_id,
                        "source_name": item.source_name,
                        "family": item.family,
                        "status": item.automation_route,
                        "normal_dxf_file_id": item.normal_dxf_file_id,
                    }
                    for item in persisted_items
                ],
            }
            ledger_path = _write_json(
                platform_directory / "PL拆板信息表.json",
                ledger_payload,
            )
            for role, path in (
                ("validation", validation_path),
                ("ledger", ledger_path),
            ):
                if not path.is_file():
                    raise PlSplitError(f"PL 拆板{role}文件未生成。")
            validation_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=f"batch/{validation_path.name}",
                path=validation_path,
                batch_name=batch_name,
                content_type="application/json",
                storage_stage="pl-xbox-split",
            )
            ledger_file = persist_split_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=f"batch/{ledger_path.name}",
                path=ledger_path,
                batch_name=batch_name,
                content_type="application/json",
                storage_stage="pl-xbox-split",
            )
            run_status = "completed_with_review" if manual_count else "completed"
            manifest_payload: dict[str, object] = {
                "schema": PL_MANIFEST_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "job_id": job.id,
                "job_attempt": attempt,
                "status": run_status,
                "splitter_version": PL_SPLITTER_VERSION,
                "cli_schema": PL_REPORT_SCHEMA,
                "validation_schema": PL_VALIDATION_SCHEMA,
                "input_manifest_sha256": manifest_sha256,
                "source_contracts": {"PL": PL_SOURCE_CONTRACT_ID},
                "input_count": len(persisted_items),
                "auto_accepted_count": auto_count,
                "manual_review_count": manual_count,
                "failed_count": failed_count,
                "split_ledger_file_id": ledger_file.id,
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
                        "weld_allowance_dxf_file_id": None,
                        "split_report_file_id": item.split_report_file_id,
                    }
                    for item in persisted_items
                ],
            }
            manifest_path = _write_json(
                platform_directory / "pl-split-manifest.json",
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
                storage_stage="pl-xbox-split",
            )
            finish_pl_split_run(
                run,
                auto_accepted_count=auto_count,
                manual_review_count=manual_count,
                failed_count=failed_count,
                ledger_file=ledger_file,
                manifest_file=manifest_file,
                validation_file=validation_file,
            )
            analysis = record_pl_split_analysis(
                db,
                job=job,
                workflow_id=workflow.id,
                run=run,
                manifest_file=manifest_file,
                validation_file=validation_file,
            )
            for artifact_type, stored, role, result_id in (
                ("split_ledger", ledger_file, "split_ledger", None),
                ("validation_report", validation_file, "validation_report", None),
                ("split_manifest", manifest_file, "split_manifest", analysis.id),
            ):
                attach_artifact(
                    db,
                    workflow,
                    stage_code="pl_xbox_split",
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
                STEP_PERSIST_PL_DXF_SPLIT,
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
                        "PL 拆板完成，部分图纸被安全拒绝"
                        if manual_count
                        else "PL 拆板及独立校验全部完成"
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
                mark_pl_split_failed(db, job_id, attempt, exc)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed to persist PL split failure for job %s", job_id)
        logger.exception("PL split failed for job %s", job_id)
    finally:
        db.close()
