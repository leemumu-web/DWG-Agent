"""Attempt-aware orchestration for the merged PL + XBOX splitting Stage.

One job attempt drives two independent Stage subprocesses (PL and XBOX);
their validated results merge into a single ``DxfSplitRun`` whose items
carry ``family="PL"`` or ``family="XBOX"``. PL keeps its single-artifact
rule; XBOX registers the normal + weld-allowance pair.
"""

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
    list_xbox_split_candidate_inputs,
)
from app.modules.dxf_splitting.persistence import (
    PL_XBOX_COMBINED_CLI_SCHEMA,
    PL_XBOX_COMBINED_SPLITTER_VERSION,
    PL_XBOX_LEDGER_SCHEMA,
    PL_XBOX_MANIFEST_SCHEMA,
    PL_XBOX_SOURCE_CONTRACTS,
    PL_XBOX_VALIDATION_SCHEMA,
    finish_pl_split_run,
    get_or_create_pl_xbox_split_run,
    load_split_run,
    mark_pl_split_failed,
    mark_split_interrupted,
    persist_split_output,
    record_pl_split_analysis,
    record_pl_split_item,
    record_xbox_split_item,
)
from app.modules.dxf_splitting.pl_adapter import (
    PL_SOURCE_CONTRACT_ID,
    PL_SPLITTER_VERSION,
    PlSplitError,
    invoke_pl_splitter,
)
from app.modules.dxf_splitting.pl_validation import validate_pl_result
from app.modules.dxf_splitting.validation import StagedSplitSource
from app.modules.dxf_splitting.xbox_adapter import (
    XBOX_SOURCE_CONTRACT_ID,
    XBOX_SPLITTER_VERSION,
    invoke_xbox_splitter,
)
from app.modules.dxf_splitting.xbox_validation import validate_xbox_result
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
    STEP_RUN_XBOX_DXF_SPLIT,
    STEP_VALIDATE_PL_DXF_SPLIT,
    STEP_VALIDATE_XBOX_DXF_SPLIT,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.time import business_now

logger = logging.getLogger(__name__)


def _temporary_directory(job_id: int, attempt: int):
    work_root = Path(settings.dxf_split_work_root)
    try:
        work_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlSplitError("服务器 PL/XBOX 拆板工作空间不可用。") from exc
    return tempfile.TemporaryDirectory(
        prefix=f"pl-xbox-split-{job_id}-{attempt}-",
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
    *,
    family: str,
) -> list[StagedSplitSource]:
    staged: list[StagedSplitSource] = []
    seen_names: set[str] = set()
    for semantic in inputs:
        if semantic.part_type != family:
            raise PlSplitError(f"{family} Stage 收到了非 {family} 分类条目。")
        stored = db.get(StoredFile, semantic.output_file_id)
        if stored is None or stored.status == "deleted" or stored.file_ext.casefold() != ".dxf":
            raise PlSplitError(
                f"{family} 分类条目 {semantic.classification_item_id} 的 DXF 不可用。"
            )
        source_name = Path(stored.original_name).name
        if source_name.casefold() in seen_names:
            raise PlSplitError(f"{family} 拆板输入文件名冲突：{source_name}")
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
    *,
    family: str,
) -> list[tuple[StagedSplitSource, dict[str, Any]]]:
    expected = {source.staged_path.resolve(): source for source in staged}
    mapped: dict[Path, dict[str, Any]] = {}
    for value in payload.get("items", []):
        if not isinstance(value, dict) or not isinstance(value.get("source"), str):
            raise PlSplitError(f"{family} 拆板报告含无效逐图记录。")
        source_path = Path(value["source"]).resolve()
        if source_path not in expected or source_path in mapped:
            raise PlSplitError(f"{family} 拆板报告与冻结输入不是一一对应。")
        mapped[source_path] = value
    if set(mapped) != set(expected):
        raise PlSplitError(f"{family} 拆板报告遗漏冻结输入。")
    return [(source, mapped[source.staged_path.resolve()]) for source in staged]


def _artifact_metadata(
    *,
    job: Job,
    run_id: int,
    role: str,
    family: str,
    split_item_id: int | None = None,
    classification_item_id: int | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "job_id": job.id,
        "job_attempt": job.attempt,
        "split_run_id": run_id,
        "family": family,
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
            message="PL/XBOX 拆板任务已接收",
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
            raise PlSplitError("Job 未绑定当前 PL/XBOX 拆板阶段。")
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
            raise PlSplitError("PL/XBOX 拆板 Job 的分类账本不一致。")
        manifest_sha256 = str((job.params_json or {}).get("input_manifest_sha256") or "")
        if not manifest_sha256 or manifest_sha256 != classification.input_manifest_sha256:
            raise PlSplitError("PL/XBOX 拆板 Job 的冻结输入摘要不一致。")
        pl_inputs = list_pl_split_candidate_inputs(db, workflow.id)
        xbox_inputs = list_xbox_split_candidate_inputs(db, workflow.id)
        if not pl_inputs and not xbox_inputs:
            raise PlSplitError("最新分类运行没有 PL/XBOX 拆板候选。")
        total_inputs = len(pl_inputs) + len(xbox_inputs)
        run = get_or_create_pl_xbox_split_run(
            db,
            job=job,
            workflow=workflow,
            classification_run_id=classification.id,
            attempt=attempt,
            manifest_sha256=manifest_sha256,
            input_count=total_inputs,
        )

        with _temporary_directory(job.id, attempt) as raw_root:
            root = Path(raw_root)
            pl_input_directory = root / "classified-pl-input"
            pl_output_directory = root / "pl-split-output"
            xbox_input_directory = root / "classified-xbox-input"
            xbox_output_directory = root / "xbox-split-output"
            platform_directory = root / "platform"
            for directory in (
                pl_input_directory,
                pl_output_directory,
                xbox_input_directory,
                xbox_output_directory,
                platform_directory,
            ):
                directory.mkdir()
            pl_staged = _stage_sources(
                db, pl_inputs, pl_input_directory, family="PL"
            )
            xbox_staged = _stage_sources(
                db, xbox_inputs, xbox_input_directory, family="XBOX"
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
                    message="PL/XBOX 输入已校验并暂存",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return

            family_runs: list[
                tuple[
                    str,
                    list[StagedSplitSource],
                    Path,
                    list[tuple[StagedSplitSource, dict[str, Any]]],
                ]
            ] = []
            if pl_staged:
                pl_result = invoke_pl_splitter(
                    pl_input_directory,
                    pl_output_directory,
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
                        "input_count": len(pl_staged),
                        "source_contract": PL_SOURCE_CONTRACT_ID,
                    },
                    output_json={
                        "exit_code": pl_result.exit_code,
                        "success_count": pl_result.payload["success_count"],
                        "rejected_count": pl_result.payload["rejected_count"],
                    },
                )
                family_runs.append(
                    (
                        "PL",
                        pl_staged,
                        pl_output_directory,
                        _result_by_source(
                            pl_staged, pl_result.payload, family="PL"
                        ),
                    )
                )
            if xbox_staged:
                xbox_result = invoke_xbox_splitter(
                    xbox_input_directory,
                    xbox_output_directory,
                    timeout_seconds=settings.dxf_split_timeout_seconds,
                )
                _add_step(
                    db,
                    job.id,
                    attempt,
                    STEP_RUN_XBOX_DXF_SPLIT,
                    worker_name,
                    input_json={
                        "splitter_version": XBOX_SPLITTER_VERSION,
                        "input_count": len(xbox_staged),
                        "source_contract": XBOX_SOURCE_CONTRACT_ID,
                    },
                    output_json={
                        "exit_code": xbox_result.exit_code,
                        "success_count": xbox_result.payload["success_count"],
                        "rejected_count": xbox_result.payload["rejected_count"],
                    },
                )
                family_runs.append(
                    (
                        "XBOX",
                        xbox_staged,
                        xbox_output_directory,
                        _result_by_source(
                            xbox_staged, xbox_result.payload, family="XBOX"
                        ),
                    )
                )

            validated_pairs: list[
                tuple[StagedSplitSource, dict[str, Any], Any]
            ] = []
            for family, _staged, output_directory, matched in family_runs:
                validator = validate_pl_result if family == "PL" else validate_xbox_result
                for source, report_item in matched:
                    validated_pairs.append(
                        (source, report_item, validator(source, report_item, output_directory))
                    )
            auto_count = sum(
                item.automation_route == "auto_accepted" for _, _, item in validated_pairs
            )
            manual_count = len(validated_pairs) - auto_count
            failed_count = manual_count
            _add_step(
                db,
                job.id,
                attempt,
                STEP_VALIDATE_PL_DXF_SPLIT
                if not xbox_staged
                else STEP_VALIDATE_XBOX_DXF_SPLIT,
                worker_name,
                input_json={"schema": PL_XBOX_VALIDATION_SCHEMA},
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
                    message="PL/XBOX 保存后独立校验完成",
                ),
            )
            if job is None:
                mark_split_interrupted(db, job_id, attempt)
                return

            run = load_split_run(db, job_id=job.id, attempt=attempt)
            batch_name = f"workflow-{workflow.id}-pl-xbox-split-attempt-{attempt}"
            persisted_items = []
            validation_items: list[dict[str, object]] = []
            for index, (source, report_item, validation) in enumerate(
                validated_pairs,
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
                outputs = portable_item.get("outputs")
                if isinstance(outputs, dict):
                    portable_item["outputs"] = {
                        key: Path(str(value)).name
                        for key, value in outputs.items()
                        if isinstance(value, str)
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
                weld_allowance_file = None
                weld_allowance_report_file = None
                if validation.automation_route == "auto_accepted":
                    if validation.normal_dxf_path is not None:
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
                    if validation.weld_allowance_dxf_path is not None:
                        weld_allowance_file = persist_split_output(
                            db,
                            job=job,
                            workflow_id=workflow.id,
                            attempt=attempt,
                            relative_path=f"normal/{validation.weld_allowance_dxf_path.name}",
                            path=validation.weld_allowance_dxf_path,
                            batch_name=batch_name,
                            content_type="application/dxf",
                            storage_stage="pl-xbox-split",
                        )
                    if validation.weld_allowance_report_path is not None:
                        weld_allowance_report_file = persist_split_output(
                            db,
                            job=job,
                            workflow_id=workflow.id,
                            attempt=attempt,
                            relative_path=(
                                f"reports/{validation.weld_allowance_report_path.name}"
                            ),
                            path=validation.weld_allowance_report_path,
                            batch_name=batch_name,
                            content_type="application/json",
                            storage_stage="pl-xbox-split",
                        )
                if validation.family == "XBOX":
                    item = record_xbox_split_item(
                        db,
                        run=run,
                        validated=validation,
                        normal_file=normal_file,
                        weld_allowance_file=weld_allowance_file,
                        item_report_file=item_report_file,
                        weld_allowance_report_file=weld_allowance_report_file,
                    )
                else:
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
                        "family": item.family,
                        "automation_route": item.automation_route,
                        "disposition": item.disposition,
                        "diagnostics": item.diagnostics_json or [],
                        "validation": item.validation_json or {},
                    }
                )
                artifact_family = item.family or validation.family or "PL"
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
                            family=artifact_family,
                        ),
                    )
                if weld_allowance_file is not None:
                    attach_artifact(
                        db,
                        workflow,
                        stage_code="pl_xbox_split",
                        artifact_type="weld_allowance_dxf",
                        file_id=weld_allowance_file.id,
                        metadata=_artifact_metadata(
                            job=job,
                            run_id=run.id,
                            split_item_id=item.id,
                            classification_item_id=item.classification_item_id,
                            role="weld_allowance_dxf",
                            family=artifact_family,
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
                        family=artifact_family,
                    ),
                )

            validation_payload: dict[str, object] = {
                "schema": PL_XBOX_VALIDATION_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "job_attempt": attempt,
                "input_manifest_sha256": manifest_sha256,
                "input_count": len(validated_pairs),
                "auto_accepted_count": auto_count,
                "manual_review_count": manual_count,
                "failed_count": failed_count,
                "items": validation_items,
            }
            validation_path = _write_json(
                platform_directory / "pl-xbox-split-validation.json",
                validation_payload,
            )
            ledger_payload: dict[str, object] = {
                "schema": PL_XBOX_LEDGER_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "source_contracts": dict(PL_XBOX_SOURCE_CONTRACTS),
                "items": [
                    {
                        "split_item_id": item.id,
                        "classification_item_id": item.classification_item_id,
                        "source_name": item.source_name,
                        "family": item.family,
                        "status": item.automation_route,
                        "normal_dxf_file_id": item.normal_dxf_file_id,
                        "weld_allowance_dxf_file_id": item.weld_allowance_dxf_file_id,
                    }
                    for item in persisted_items
                ],
            }
            ledger_path = _write_json(
                platform_directory / "PL_XBOX拆板信息表.json",
                ledger_payload,
            )
            for role, path in (
                ("validation", validation_path),
                ("ledger", ledger_path),
            ):
                if not path.is_file():
                    raise PlSplitError(f"PL/XBOX 拆板{role}文件未生成。")
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
                "schema": PL_XBOX_MANIFEST_SCHEMA,
                "workflow_id": workflow.id,
                "split_run_id": run.id,
                "job_id": job.id,
                "job_attempt": attempt,
                "status": run_status,
                "splitter_versions": {
                    "PL": PL_SPLITTER_VERSION,
                    "XBOX": XBOX_SPLITTER_VERSION,
                },
                "splitter_version": PL_XBOX_COMBINED_SPLITTER_VERSION,
                "cli_schema": PL_XBOX_COMBINED_CLI_SCHEMA,
                "validation_schema": PL_XBOX_VALIDATION_SCHEMA,
                "input_manifest_sha256": manifest_sha256,
                "source_contracts": dict(PL_XBOX_SOURCE_CONTRACTS),
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
                        "family": item.family,
                        "automation_route": item.automation_route,
                        "normal_dxf_file_id": item.normal_dxf_file_id,
                        "weld_allowance_dxf_file_id": item.weld_allowance_dxf_file_id,
                        "split_report_file_id": item.split_report_file_id,
                    }
                    for item in persisted_items
                ],
            }
            manifest_path = _write_json(
                platform_directory / "pl-xbox-split-manifest.json",
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
                        family="PL",
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
                        "PL/XBOX 拆板完成，部分图纸被安全拒绝"
                        if manual_count
                        else "PL/XBOX 拆板及独立校验全部完成"
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
