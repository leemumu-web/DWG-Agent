"""Whole-Workflow backup manifests and safe retention state transitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.cad_processing.interface import preview_batch_name
from app.modules.dxf_classification.interface import DxfClassificationItem, DxfClassificationRun
from app.modules.dxf_splitting.interface import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.files.interface import (
    FileTransfer,
    StorageZipMember,
    StoredFile,
    TransferSpec,
    mark_transfer_in_progress,
    prepare_transfer_in_transaction,
    settle_transfer,
)
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRetentionExport,
    WorkflowRun,
    WorkflowStageRun,
)
from app.platform.config.settings import settings
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import (
    AbstractStorageBackend,
    StorageError,
    StorageObjectNotFound,
)

TERMINAL_WORKFLOW_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_EXECUTION_STATUSES = {"queued", "running"}
RETENTION_COOKIE_NAME = "dwg_workflow_retention"
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class RetentionScope:
    manifest: tuple[dict[str, Any], ...]
    manifest_bytes: bytes
    manifest_sha256: str
    preview_file_ids: tuple[int, ...]
    file_count: int
    preview_cache_count: int
    source_size_bytes: int
    reclaimable_size_bytes: int
    blockers: tuple[dict[str, Any], ...]


def _add_reference(
    references: dict[int, list[dict[str, str]]],
    file_id: int | None,
    *,
    kind: str,
    role: str = "",
    stage_code: str = "",
    artifact_type: str = "",
) -> None:
    if file_id is None:
        return
    references[int(file_id)].append(
        {
            "kind": kind,
            "role": role,
            "stage_code": stage_code,
            "artifact_type": artifact_type,
        }
    )


def _safe_segment(value: str, fallback: str) -> str:
    normalized = value.strip()
    return normalized if _SAFE_SEGMENT_RE.fullmatch(normalized) else fallback


def _safe_original_name(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or PurePosixPath(value).name != value
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_RETENTION_FILENAME_INVALID",
            "登记文件名无法安全写入完整备份，请先修复文件登记。",
        )
    return value


def _archive_path(stored: StoredFile, references: list[dict[str, str]]) -> str:
    original_name = _safe_original_name(stored.original_name)
    input_reference = next(
        (item for item in references if item["kind"] == "input"),
        None,
    )
    if input_reference:
        role = _safe_segment(input_reference["role"], "unknown")
        return f"输入/{role}/{stored.id}/{original_name}"
    artifact_reference = next(
        (item for item in references if item["kind"] == "artifact"),
        None,
    )
    if artifact_reference:
        stage_code = _safe_segment(artifact_reference["stage_code"], "unknown")
        artifact_type = _safe_segment(
            artifact_reference["artifact_type"],
            "unknown",
        )
        return f"阶段产物/{stage_code}/{artifact_type}/{stored.id}/{original_name}"
    return f"其他结果/{stored.id}/{original_name}"


def _scalar_file_ids(db: Session, statement) -> set[int]:
    return {int(value) for value in db.scalars(statement).all() if value is not None}


def _shared_file_ids(db: Session, workflow_id: int, file_ids: set[int]) -> set[int]:
    if not file_ids:
        return set()
    shared: set[int] = set()
    input_scope = WorkflowInputBatch.workflow_run_id != workflow_id
    for column in (WorkflowInputItem.file_id, WorkflowInputItem.derived_dxf_file_id):
        shared.update(
            _scalar_file_ids(
                db,
                select(column)
                .join(
                    WorkflowInputBatch,
                    WorkflowInputBatch.id == WorkflowInputItem.input_batch_id,
                )
                .where(input_scope, column.in_(file_ids)),
            )
        )
    shared.update(
        _scalar_file_ids(
            db,
            select(WorkflowArtifact.file_id).where(
                WorkflowArtifact.workflow_run_id != workflow_id,
                WorkflowArtifact.file_id.in_(file_ids),
            ),
        )
    )
    shared.update(
        _scalar_file_ids(
            db,
            select(AnalysisResult.result_file_id)
            .join(
                WorkflowStageRun,
                WorkflowStageRun.job_id == AnalysisResult.job_id,
            )
            .where(
                WorkflowStageRun.workflow_run_id != workflow_id,
                AnalysisResult.result_file_id.in_(file_ids),
            ),
        )
    )
    shared.update(
        _scalar_file_ids(
            db,
            select(AnalysisResult.result_file_id)
            .join(
                WorkflowInputItem,
                WorkflowInputItem.conversion_job_id == AnalysisResult.job_id,
            )
            .join(
                WorkflowInputBatch,
                WorkflowInputBatch.id == WorkflowInputItem.input_batch_id,
            )
            .where(
                WorkflowInputBatch.workflow_run_id != workflow_id,
                AnalysisResult.result_file_id.in_(file_ids),
            ),
        )
    )

    classification_columns = (
        DxfClassificationRun.report_file_id,
        DxfClassificationRun.manifest_file_id,
    )
    for column in classification_columns:
        shared.update(
            _scalar_file_ids(
                db,
                select(column).where(
                    DxfClassificationRun.workflow_run_id != workflow_id,
                    column.in_(file_ids),
                ),
            )
        )
    for column in (
        DxfClassificationItem.source_file_id,
        DxfClassificationItem.output_file_id,
    ):
        shared.update(
            _scalar_file_ids(
                db,
                select(column)
                .join(
                    DxfClassificationRun,
                    DxfClassificationRun.id == DxfClassificationItem.run_id,
                )
                .where(
                    DxfClassificationRun.workflow_run_id != workflow_id,
                    column.in_(file_ids),
                ),
            )
        )

    for column in (
        DxfSplitRun.bh_split_ledger_file_id,
        DxfSplitRun.split_manifest_file_id,
        DxfSplitRun.validation_report_file_id,
    ):
        shared.update(
            _scalar_file_ids(
                db,
                select(column).where(
                    DxfSplitRun.workflow_run_id != workflow_id,
                    column.in_(file_ids),
                ),
            )
        )
    split_item_columns = (
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
    for column in split_item_columns:
        shared.update(
            _scalar_file_ids(
                db,
                select(column)
                .join(DxfSplitRun, DxfSplitRun.id == DxfSplitItem.run_id)
                .where(
                    DxfSplitRun.workflow_run_id != workflow_id,
                    column.in_(file_ids),
                ),
            )
        )
    for column in (
        DxfSplitReviewDecision.final_normal_dxf_file_id,
        DxfSplitReviewDecision.final_weld_allowance_dxf_file_id,
    ):
        shared.update(
            _scalar_file_ids(
                db,
                select(column)
                .join(
                    DxfSplitItem,
                    DxfSplitItem.id == DxfSplitReviewDecision.split_item_id,
                )
                .join(DxfSplitRun, DxfSplitRun.id == DxfSplitItem.run_id)
                .where(
                    DxfSplitRun.workflow_run_id != workflow_id,
                    column.in_(file_ids),
                ),
            )
        )
    return shared


def build_retention_scope(db: Session, workflow: WorkflowRun) -> RetentionScope:
    references: dict[int, list[dict[str, str]]] = defaultdict(list)
    job_ids: set[int] = set()
    blockers: list[dict[str, Any]] = []

    if workflow.status not in TERMINAL_WORKFLOW_STATUSES:
        blockers.append(
            {
                "code": "WORKFLOW_RETENTION_NOT_TERMINAL",
                "message": "只有已成功、已失败或已取消的生产流程可以整批释放存储。",
                "details": {"status": workflow.status},
            }
        )

    stages = list(
        db.scalars(
            select(WorkflowStageRun)
            .where(WorkflowStageRun.workflow_run_id == workflow.id)
            .order_by(WorkflowStageRun.sequence, WorkflowStageRun.id)
        ).all()
    )
    stages_by_id = {stage.id: stage for stage in stages}
    active_stages = [stage.stage_code for stage in stages if stage.status in ACTIVE_EXECUTION_STATUSES]
    if active_stages:
        blockers.append(
            {
                "code": "WORKFLOW_RETENTION_ACTIVE_STAGE",
                "message": "仍有阶段正在排队或处理，不能整批释放存储。",
                "details": {"stage_codes": active_stages},
            }
        )
    job_ids.update(stage.job_id for stage in stages if stage.job_id is not None)

    input_items = list(
        db.scalars(
            select(WorkflowInputItem)
            .join(
                WorkflowInputBatch,
                WorkflowInputBatch.id == WorkflowInputItem.input_batch_id,
            )
            .where(WorkflowInputBatch.workflow_run_id == workflow.id)
            .order_by(WorkflowInputItem.id)
        ).all()
    )
    for item in input_items:
        _add_reference(references, item.file_id, kind="input", role=item.role)
        _add_reference(
            references,
            item.derived_dxf_file_id,
            kind="input",
            role="derived_dxf",
        )
        if item.conversion_job_id is not None:
            job_ids.add(item.conversion_job_id)

    artifacts = list(
        db.scalars(
            select(WorkflowArtifact)
            .where(WorkflowArtifact.workflow_run_id == workflow.id)
            .order_by(WorkflowArtifact.id)
        ).all()
    )
    artifact_result_ids: set[int] = set()
    for artifact in artifacts:
        stage = stages_by_id.get(artifact.stage_run_id)
        _add_reference(
            references,
            artifact.file_id,
            kind="artifact",
            stage_code=stage.stage_code if stage else "unknown",
            artifact_type=artifact.artifact_type,
        )
        if artifact.result_id is not None:
            artifact_result_ids.add(artifact.result_id)

    if artifact_result_ids:
        for file_id in db.scalars(
            select(AnalysisResult.result_file_id).where(
                AnalysisResult.id.in_(artifact_result_ids),
                AnalysisResult.result_file_id.is_not(None),
            )
        ).all():
            _add_reference(references, file_id, kind="result")

    classification_runs = list(
        db.scalars(
            select(DxfClassificationRun)
            .where(DxfClassificationRun.workflow_run_id == workflow.id)
            .order_by(DxfClassificationRun.job_attempt, DxfClassificationRun.id)
        ).all()
    )
    classification_run_ids = {run.id for run in classification_runs}
    for run in classification_runs:
        job_ids.add(run.job_id)
        _add_reference(references, run.report_file_id, kind="ledger")
        _add_reference(references, run.manifest_file_id, kind="ledger")
    if classification_run_ids:
        for item in db.scalars(
            select(DxfClassificationItem)
            .where(DxfClassificationItem.run_id.in_(classification_run_ids))
            .order_by(DxfClassificationItem.id)
        ).all():
            _add_reference(references, item.source_file_id, kind="ledger")
            _add_reference(references, item.output_file_id, kind="result")

    split_runs = list(
        db.scalars(
            select(DxfSplitRun)
            .where(DxfSplitRun.workflow_run_id == workflow.id)
            .order_by(DxfSplitRun.job_attempt, DxfSplitRun.id)
        ).all()
    )
    split_run_ids = {run.id for run in split_runs}
    for run in split_runs:
        job_ids.add(run.job_id)
        for file_id in (
            run.bh_split_ledger_file_id,
            run.split_manifest_file_id,
            run.validation_report_file_id,
        ):
            _add_reference(references, file_id, kind="ledger")
    split_item_ids: set[int] = set()
    if split_run_ids:
        for item in db.scalars(
            select(DxfSplitItem)
            .where(DxfSplitItem.run_id.in_(split_run_ids))
            .order_by(DxfSplitItem.id)
        ).all():
            split_item_ids.add(item.id)
            for file_id in (
                item.source_file_id,
                item.normal_dxf_file_id,
                item.weld_allowance_dxf_file_id,
                item.split_report_file_id,
                item.weld_allowance_report_file_id,
                item.candidate_normal_dxf_file_id,
                item.candidate_weld_allowance_dxf_file_id,
                item.candidate_split_report_file_id,
                item.candidate_weld_allowance_report_file_id,
            ):
                _add_reference(references, file_id, kind="result")
    if split_item_ids:
        for decision in db.scalars(
            select(DxfSplitReviewDecision)
            .where(DxfSplitReviewDecision.split_item_id.in_(split_item_ids))
            .order_by(DxfSplitReviewDecision.id)
        ).all():
            _add_reference(
                references,
                decision.final_normal_dxf_file_id,
                kind="result",
            )
            _add_reference(
                references,
                decision.final_weld_allowance_dxf_file_id,
                kind="result",
            )

    if job_ids:
        active_jobs = list(
            db.execute(
                select(Job.id, Job.status).where(
                    Job.id.in_(job_ids),
                    Job.status.in_(ACTIVE_EXECUTION_STATUSES),
                )
            ).all()
        )
        if active_jobs:
            blockers.append(
                {
                    "code": "WORKFLOW_RETENTION_ACTIVE_JOB",
                    "message": "仍有任务正在排队或处理，不能整批释放存储。",
                    "details": {
                        "jobs": [
                            {"job_id": job_id, "status": status}
                            for job_id, status in active_jobs
                        ]
                    },
                }
            )
        for file_id in db.scalars(
            select(AnalysisResult.result_file_id)
            .where(
                AnalysisResult.job_id.in_(job_ids),
                AnalysisResult.result_file_id.is_not(None),
            )
            .order_by(AnalysisResult.id)
        ).all():
            _add_reference(references, file_id, kind="result")

    file_ids = set(references)
    stored_by_id = {
        stored.id: stored
        for stored in db.scalars(
            select(StoredFile).where(StoredFile.id.in_(file_ids)).order_by(StoredFile.id)
        ).all()
    } if file_ids else {}
    missing_ids = sorted(file_ids - set(stored_by_id))
    if missing_ids:
        blockers.append(
            {
                "code": "WORKFLOW_RETENTION_FILE_REGISTRATION_MISSING",
                "message": "生产关系引用了不存在的文件登记，需先修复数据关系。",
                "details": {"file_ids": missing_ids[:20], "missing_count": len(missing_ids)},
            }
        )
    unavailable_ids = sorted(
        file_id for file_id, stored in stored_by_id.items() if stored.status != "available"
    )
    if unavailable_ids:
        blockers.append(
            {
                "code": "WORKFLOW_RETENTION_FILES_UNAVAILABLE",
                "message": "完整备份范围内存在不可用或已删除文件，不能永久清理。",
                "details": {
                    "file_ids": unavailable_ids[:20],
                    "unavailable_count": len(unavailable_ids),
                },
            }
        )

    shared_ids = sorted(_shared_file_ids(db, workflow.id, file_ids))
    if shared_ids:
        blockers.append(
            {
                "code": "WORKFLOW_RETENTION_SHARED_FILES",
                "message": "部分文件同时被其他生产流程引用，不能随当前流程永久删除。",
                "details": {
                    "file_ids": shared_ids[:20],
                    "shared_file_count": len(shared_ids),
                },
            }
        )

    manifest = []
    for file_id in sorted(stored_by_id):
        stored = stored_by_id[file_id]
        item_references = sorted(
            references[file_id],
            key=lambda item: (
                {"input": 0, "artifact": 1}.get(item["kind"], 2),
                item["role"],
                item["stage_code"],
                item["artifact_type"],
            ),
        )
        manifest.append(
            {
                "file_id": stored.id,
                "archive_path": _archive_path(stored, item_references),
                "bucket": stored.bucket,
                "storage_key": stored.storage_key,
                "original_name": stored.original_name,
                "size_bytes": stored.size_bytes,
                "sha256": stored.sha256,
                "references": item_references,
            }
        )
    manifest.sort(key=lambda item: (item["archive_path"], item["file_id"]))
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    preview_batch_names = [
        preview_batch_name(stored)
        for stored in stored_by_id.values()
        if stored.file_ext.lower() == ".dxf"
    ]
    previews = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name.in_(preview_batch_names),
                StoredFile.file_ext == ".svg",
                StoredFile.status == "available",
            )
        ).all()
    ) if preview_batch_names else []
    source_size = sum(item["size_bytes"] for item in manifest)
    return RetentionScope(
        manifest=tuple(manifest),
        manifest_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        preview_file_ids=tuple(sorted({preview.id for preview in previews})),
        file_count=len(manifest),
        preview_cache_count=len({preview.id for preview in previews}),
        source_size_bytes=source_size,
        reclaimable_size_bytes=source_size
        + sum(preview.size_bytes for preview in {row.id: row for row in previews}.values()),
        blockers=tuple(blockers),
    )


def _raise_first_blocker(scope: RetentionScope) -> None:
    if not scope.blockers:
        return
    blocker = scope.blockers[0]
    raise AppHTTPException(
        409,
        str(blocker["code"]),
        str(blocker["message"]),
        dict(blocker.get("details") or {}),
    )


def create_retention_export(
    db: Session,
    workflow: WorkflowRun,
    *,
    actor_user_id: int,
    storage: AbstractStorageBackend,
) -> tuple[WorkflowRetentionExport, str]:
    scope = build_retention_scope(db, workflow)
    _raise_first_blocker(scope)
    if not scope.manifest:
        raise AppHTTPException(
            409,
            "WORKFLOW_RETENTION_EMPTY",
            "当前生产流程没有可备份的登记文件。",
        )
    for item in scope.manifest:
        try:
            observed = storage.stat_object(str(item["bucket"]), str(item["storage_key"]))
        except StorageObjectNotFound as exc:
            raise AppHTTPException(
                409,
                "WORKFLOW_RETENTION_OBJECT_MISSING",
                "完整备份范围内有对象缺失，请先执行一致性检查。",
                {"file_id": item["file_id"]},
            ) from exc
        except StorageError as exc:
            raise AppHTTPException(
                503,
                "WORKFLOW_RETENTION_STORAGE_UNAVAILABLE",
                "对象存储暂不可用，未创建完整备份。",
            ) from exc
        if observed.size_bytes != int(item["size_bytes"]):
            raise AppHTTPException(
                409,
                "WORKFLOW_RETENTION_OBJECT_MISMATCH",
                "对象大小与 MySQL 登记不一致，请先执行一致性检查。",
                {
                    "file_id": item["file_id"],
                    "registered_bytes": item["size_bytes"],
                    "observed_bytes": observed.size_bytes,
                },
            )

    token = secrets.token_urlsafe(32)
    row = WorkflowRetentionExport(
        export_uid=str(uuid4()),
        workflow_run_id=workflow.id,
        created_by=actor_user_id,
        status="prepared",
        manifest_json=list(scope.manifest),
        manifest_sha256=scope.manifest_sha256,
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        token_expires_at=datetime.now(UTC)
        + timedelta(minutes=settings.workflow_batch_export_ttl_minutes),
        file_count=scope.file_count,
        preview_cache_count=scope.preview_cache_count,
        source_size_bytes=scope.source_size_bytes,
        reclaimable_size_bytes=scope.reclaimable_size_bytes,
    )
    db.add(row)
    db.flush()
    return row, token


def retention_filename(workflow_id: int) -> str:
    return f"workflow-{workflow_id}-完整备份.zip"


def retention_download_path(workflow_id: int, export_uid: str) -> str:
    return (
        f"{settings.api_v1_prefix}/workflows/{workflow_id}/retention-exports/"
        f"{export_uid}/download"
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_retention_token(row: WorkflowRetentionExport, token: str | None) -> None:
    if (
        not token
        or not row.token_digest
        or not hmac.compare_digest(
            row.token_digest,
            hashlib.sha256(token.encode()).hexdigest(),
        )
    ):
        raise AppHTTPException(
            403,
            "WORKFLOW_RETENTION_TOKEN_INVALID",
            "本次完整备份的下载凭据无效。",
        )
    if _as_utc(row.token_expires_at) < datetime.now(UTC):
        raise AppHTTPException(
            410,
            "WORKFLOW_RETENTION_TOKEN_EXPIRED",
            "本次完整备份的下载凭据已过期，请重新创建。",
        )


def load_retention_export(
    db: Session,
    workflow_id: int,
    export_uid: str,
    *,
    for_update: bool = False,
) -> WorkflowRetentionExport:
    statement = select(WorkflowRetentionExport).where(
        WorkflowRetentionExport.workflow_run_id == workflow_id,
        WorkflowRetentionExport.export_uid == export_uid,
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise AppHTTPException(404, "NOT_FOUND", "WorkflowRetentionExport not found.")
    return row


def _manifest_bytes(manifest: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _retention_manifest(
    row: WorkflowRetentionExport,
) -> tuple[list[dict[str, Any]], tuple[int, ...]]:
    raw = row.manifest_json
    if not isinstance(raw, list) or not raw:
        raise _stale_retention_export()
    if hashlib.sha256(_manifest_bytes(raw)).hexdigest() != row.manifest_sha256:
        raise _stale_retention_export()
    file_ids: list[int] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _stale_retention_export()
        try:
            file_id = int(item["file_id"])
        except (KeyError, TypeError, ValueError):
            raise _stale_retention_export() from None
        if file_id < 1 or file_id in file_ids:
            raise _stale_retention_export()
        file_ids.append(file_id)
    if len(file_ids) != row.file_count:
        raise _stale_retention_export()
    return raw, tuple(file_ids)


def _manifest_item_matches(stored: StoredFile, item: dict[str, Any]) -> bool:
    try:
        references = item["references"]
        return bool(
            isinstance(references, list)
            and stored.id == int(item["file_id"])
            and stored.bucket == item["bucket"]
            and stored.storage_key == item["storage_key"]
            and stored.original_name == item["original_name"]
            and stored.size_bytes == int(item["size_bytes"])
            and stored.sha256 == item["sha256"]
            and item["archive_path"] == _archive_path(stored, references)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _stale_retention_export() -> AppHTTPException:
    return AppHTTPException(
        409,
        "WORKFLOW_RETENTION_MANIFEST_STALE",
        "完整备份清单与当前生产关系不一致，请重新预检并创建备份。",
    )


def storage_members_for_retention(
    db: Session,
    row: WorkflowRetentionExport,
) -> list[StorageZipMember]:
    manifest, file_ids = _retention_manifest(row)
    stored_by_id = {
        stored.id: stored
        for stored in db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(file_ids),
                StoredFile.status == "available",
            )
        ).all()
    }
    members: list[StorageZipMember] = []
    for item in manifest:
        stored = stored_by_id.get(int(item["file_id"]))
        if stored is None or not _manifest_item_matches(stored, item):
            raise _stale_retention_export()
        members.append(
            StorageZipMember(
                bucket=stored.bucket,
                storage_key=stored.storage_key,
                archive_path=str(item["archive_path"]),
                expected_size_bytes=stored.size_bytes,
                expected_sha256=stored.sha256,
            )
        )
    return members


def mark_retention_download_result(
    factory: sessionmaker[Session],
    export_uid: str,
    *,
    succeeded: bool,
) -> None:
    with factory.begin() as db:
        row = db.scalar(
            select(WorkflowRetentionExport)
            .where(WorkflowRetentionExport.export_uid == export_uid)
            .with_for_update()
        )
        if row is None or row.status == "purged":
            return
        if succeeded:
            row.status = "downloaded"
            row.downloaded_at = row.downloaded_at or utcnow()
            row.error_code = None
            row.error_message = None
        else:
            row.status = "download_failed"
            row.error_code = "WORKFLOW_RETENTION_DOWNLOAD_FAILED"
            row.error_message = "完整备份未完整传输，服务器文件已保留。"


def track_retention_stream(
    factory: sessionmaker[Session],
    export_uid: str,
    chunks,
):
    try:
        yield from chunks
    except BaseException:
        mark_retention_download_result(factory, export_uid, succeeded=False)
        raise
    else:
        mark_retention_download_result(factory, export_uid, succeeded=True)


def retention_download_succeeded(db: Session, export_uid: str) -> bool:
    return (
        db.scalar(
            select(FileTransfer.id)
            .where(
                FileTransfer.operation == "workflow_retention_export",
                FileTransfer.batch_ref == export_uid,
                FileTransfer.status == "succeeded",
            )
            .limit(1)
        )
        is not None
    )


def validate_retention_purge(
    db: Session,
    workflow: WorkflowRun,
    row: WorkflowRetentionExport,
) -> None:
    if row.status not in {"downloaded", "purge_failed"} or not retention_download_succeeded(
        db,
        row.export_uid,
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_RETENTION_NOT_DOWNLOADED",
            "只有服务端确认完整备份传输成功后，才允许永久删除。",
        )
    scope = build_retention_scope(db, workflow)
    _raise_first_blocker(scope)
    if scope.manifest_sha256 != row.manifest_sha256:
        raise _stale_retention_export()


def _mark_purge_failed(
    factory: sessionmaker[Session],
    export_uid: str,
    *,
    code: str,
    message: str,
) -> None:
    with factory.begin() as db:
        row = db.scalar(
            select(WorkflowRetentionExport)
            .where(WorkflowRetentionExport.export_uid == export_uid)
            .with_for_update()
        )
        if row is not None and row.status != "purged":
            row.status = "purge_failed"
            row.error_code = code
            row.error_message = message[:1000]


def execute_retention_purge(
    export_uid: str,
    *,
    factory: sessionmaker[Session],
    storage: AbstractStorageBackend | None = None,
) -> dict[str, int | str]:
    storage = storage or storage_factory.get_storage_backend()
    transfer_uid: str | None = None
    targets: list[tuple[int, str, str, int]] = []
    source_file_ids: tuple[int, ...] = ()
    workflow_id = 0
    actor_user_id = 0
    try:
        with factory.begin() as db:
            row = db.scalar(
                select(WorkflowRetentionExport)
                .where(WorkflowRetentionExport.export_uid == export_uid)
                .with_for_update()
            )
            if row is None:
                raise AppHTTPException(404, "NOT_FOUND", "WorkflowRetentionExport not found.")
            if row.status == "purged":
                return {
                    "status": "purged",
                    "purged_file_count": row.purged_file_count,
                    "purged_size_bytes": row.purged_size_bytes,
                }
            if row.status not in {"purge_queued", "purging", "purge_failed"}:
                raise AppHTTPException(
                    409,
                    "WORKFLOW_RETENTION_PURGE_NOT_QUEUED",
                    "完整备份尚未进入永久删除队列。",
                )
            workflow = db.get(WorkflowRun, row.workflow_run_id)
            if workflow is None:
                raise AppHTTPException(404, "NOT_FOUND", "WorkflowRun not found.")
            scope = build_retention_scope(db, workflow)
            _raise_first_blocker(scope)
            if scope.manifest_sha256 != row.manifest_sha256:
                raise _stale_retention_export()
            manifest, source_file_ids = _retention_manifest(row)
            source_rows = list(
                db.scalars(
                    select(StoredFile)
                    .where(StoredFile.id.in_(source_file_ids))
                    .with_for_update()
                ).all()
            )
            source_by_id = {stored.id: stored for stored in source_rows}
            for item in manifest:
                stored = source_by_id.get(int(item["file_id"]))
                if (
                    stored is None
                    or stored.status != "available"
                    or not _manifest_item_matches(stored, item)
                ):
                    raise _stale_retention_export()
            preview_rows = list(
                db.scalars(
                    select(StoredFile).where(StoredFile.id.in_(scope.preview_file_ids))
                ).all()
            ) if scope.preview_file_ids else []
            all_rows = {stored.id: stored for stored in [*source_rows, *preview_rows]}
            targets = [
                (stored.id, stored.bucket, stored.storage_key, stored.size_bytes)
                for stored in all_rows.values()
            ]
            expected_bytes = sum(item[3] for item in targets)
            transfer = prepare_transfer_in_transaction(
                db,
                TransferSpec(
                    direction="internal",
                    operation="workflow_retention_purge",
                    actor_user_id=row.created_by,
                    request_id=f"retention-purge:{export_uid}",
                    idempotency_key=f"retention-purge:{export_uid}:{uuid4().hex}",
                    batch_ref=export_uid,
                    bucket="multiple",
                    storage_key=f"workflow-retention/{export_uid}",
                    original_name=retention_filename(workflow.id),
                    expected_bytes=expected_bytes,
                ),
            )
            transfer_uid = transfer.transfer_uid
            workflow_id = workflow.id
            actor_user_id = row.created_by
            row.status = "purging"
            row.purge_transfer_uid = transfer_uid
            row.purge_started_at = row.purge_started_at or utcnow()
            row.error_code = None
            row.error_message = None

        mark_transfer_in_progress(
            factory,
            transfer_uid,
            bucket="multiple",
            storage_key=f"workflow-retention/{export_uid}",
            expected_bytes=sum(item[3] for item in targets),
        )

        deleted_bytes = 0
        try:
            for _file_id, bucket, storage_key, size_bytes in targets:
                storage.delete_object(bucket, storage_key)
                deleted_bytes += size_bytes
        except Exception as exc:
            settle_transfer(
                factory,
                transfer_uid,
                status="compensation_required" if deleted_bytes else "failed",
                transferred_bytes=deleted_bytes,
                error_code=(
                    "WORKFLOW_RETENTION_PURGE_PARTIAL"
                    if deleted_bytes
                    else "STORAGE_DELETE_FAILED"
                ),
                error_message="完整流程永久删除未能移除全部对象。",
            )
            _mark_purge_failed(
                factory,
                export_uid,
                code="WORKFLOW_RETENTION_PURGE_PARTIAL"
                if deleted_bytes
                else "WORKFLOW_RETENTION_PURGE_FAILED",
                message="部分对象未能删除；请按请求编号核对流水后重试。",
            )
            raise AppHTTPException(
                503,
                "WORKFLOW_RETENTION_PURGE_FAILED",
                "永久删除未能完成，生产关系仍保留，请核对流水后重试。",
                {"deleted_bytes": deleted_bytes},
            ) from exc

        try:
            with factory.begin() as db:
                row = db.scalar(
                    select(WorkflowRetentionExport)
                    .where(WorkflowRetentionExport.export_uid == export_uid)
                    .with_for_update()
                )
                if row is None:
                    raise RuntimeError("Retention export disappeared during purge.")
                stored_rows = list(
                    db.scalars(
                        select(StoredFile)
                        .where(StoredFile.id.in_([item[0] for item in targets]))
                        .with_for_update()
                    ).all()
                )
                now = utcnow()
                for stored in stored_rows:
                    stored.status = "deleted"
                    stored.deleted_at = now
                    stored.purged_at = now
                db.execute(
                    delete(WorkflowArtifact).where(
                        WorkflowArtifact.workflow_run_id == workflow_id,
                        WorkflowArtifact.file_id.in_(source_file_ids),
                    )
                )
                row.status = "purged"
                row.purged_at = now
                row.purged_file_count = len(stored_rows)
                row.purged_size_bytes = deleted_bytes
                row.manifest_json = []
                row.token_digest = None
                row.error_code = None
                row.error_message = None
        except Exception as exc:
            settle_transfer(
                factory,
                transfer_uid,
                status="compensation_required",
                transferred_bytes=deleted_bytes,
                error_code="PURGE_METADATA_COMMIT_FAILED",
                error_message="对象已删除，但数据库墓碑未能提交。",
            )
            _mark_purge_failed(
                factory,
                export_uid,
                code="WORKFLOW_RETENTION_METADATA_COMMIT_FAILED",
                message="对象可能已删除，但数据库墓碑未提交；请执行一致性检查。",
            )
            raise AppHTTPException(
                503,
                "WORKFLOW_RETENTION_METADATA_COMMIT_FAILED",
                "对象与数据库状态需要补偿处理，请执行一致性检查。",
            ) from exc

        settle_transfer(
            factory,
            transfer_uid,
            status="succeeded",
            transferred_bytes=deleted_bytes,
        )
        return {
            "status": "purged",
            "purged_file_count": len(targets),
            "purged_size_bytes": deleted_bytes,
            "workflow_id": workflow_id,
            "actor_user_id": actor_user_id,
        }
    except AppHTTPException as exc:
        if transfer_uid is None:
            _mark_purge_failed(
                factory,
                export_uid,
                code=str(exc.detail.get("code", "WORKFLOW_RETENTION_PURGE_FAILED")),
                message=str(exc.detail.get("message", "永久删除预检失败。")),
            )
        raise


__all__ = [
    "RETENTION_COOKIE_NAME",
    "RetentionScope",
    "build_retention_scope",
    "create_retention_export",
    "execute_retention_purge",
    "load_retention_export",
    "mark_retention_download_result",
    "require_retention_token",
    "retention_download_path",
    "retention_filename",
    "storage_members_for_retention",
    "track_retention_stream",
    "validate_retention_purge",
]
