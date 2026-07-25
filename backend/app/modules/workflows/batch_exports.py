"""Selective workflow export manifests, download tracking and permanent object purge."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.cad_processing.interface import preview_batch_name
from app.modules.dxf_classification.interface import DxfClassificationItem, DxfClassificationRun
from app.modules.dxf_splitting.interface import DxfSplitItem, DxfSplitRun
from app.modules.files.interface import (
    FileTransfer,
    StorageZipMember,
    StoredFile,
    TransferSpec,
    complete_transfer_in_transaction,
    prepare_destructive_transfer,
    register_pending_destructive_transfer,
    session_factory_for,
    settle_transfer,
)
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowBatchExport,
    WorkflowInputItem,
    WorkflowRun,
)
from app.platform.config.settings import settings
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException, forbidden, not_found
from app.platform.storage import factory as storage_factory

EXPORT_COOKIE_NAME = "dwg_workflow_export"
EXPORT_CATEGORY_ORDER = (
    "classified_dxf",
    "processed_dxf",
    "source_excel",
    "stage1_excel",
)
EXPORT_CATEGORY_DEFINITIONS = {
    "classified_dxf": {"label": "原 DXF", "folder": "原DXF"},
    "processed_dxf": {"label": "正常拆板 DXF", "folder": "正常拆板DXF"},
    "source_excel": {"label": "原 Excel", "folder": "原Excel"},
    "stage1_excel": {"label": "产出 Excel", "folder": "产出Excel"},
}
ACTIVE_STAGE_STATUSES = {"queued", "running"}


def _stage(workflow: WorkflowRun, code: str):
    return next((item for item in workflow.stages if item.stage_code == code), None)


def _current_classified_file_ids(db: Session, workflow: WorkflowRun) -> list[int]:
    stage = _stage(workflow, "dxf_classification")
    if stage is None or stage.job_id is None or stage.job_attempt is None:
        return []
    run = db.scalar(
        select(DxfClassificationRun).where(
            DxfClassificationRun.workflow_run_id == workflow.id,
            DxfClassificationRun.job_id == stage.job_id,
            DxfClassificationRun.job_attempt == stage.job_attempt,
            DxfClassificationRun.status.in_({"completed", "completed_with_review"}),
        )
    )
    if run is None:
        return []
    return list(
        db.scalars(
            select(DxfClassificationItem.output_file_id)
            .where(DxfClassificationItem.run_id == run.id)
            .order_by(DxfClassificationItem.id)
        ).all()
    )


def _current_processed_file_ids(db: Session, workflow: WorkflowRun) -> list[int]:
    stage = _stage(workflow, "drawing_processing")
    if stage is None or stage.job_id is None or stage.job_attempt is None:
        return []
    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.workflow_run_id == workflow.id,
            DxfSplitRun.job_id == stage.job_id,
            DxfSplitRun.job_attempt == stage.job_attempt,
            DxfSplitRun.status == "completed",
        )
    )
    if run is None:
        return []
    return list(
        db.scalars(
            select(DxfSplitItem.normal_dxf_file_id)
            .where(
                DxfSplitItem.run_id == run.id,
                DxfSplitItem.normal_dxf_file_id.is_not(None),
            )
            .order_by(DxfSplitItem.id)
        ).all()
    )


def _source_excel_file_ids(db: Session, workflow: WorkflowRun) -> list[int]:
    batch = workflow.input_batch
    if batch is None or batch.status != "frozen":
        return []
    return list(
        db.scalars(
            select(WorkflowInputItem.file_id)
            .where(
                WorkflowInputItem.input_batch_id == batch.id,
                WorkflowInputItem.role == "source_excel",
            )
            .order_by(WorkflowInputItem.id)
        ).all()
    )


def _stage1_excel_file_ids(db: Session, workflow: WorkflowRun) -> list[int]:
    stage = _stage(workflow, "excel_stage1")
    if stage is None or stage.job_id is None or stage.job_attempt is None:
        return []
    job = db.get(Job, stage.job_id)
    if job is None or job.attempt != stage.job_attempt or job.status != "succeeded":
        return []
    # A retried Job keeps older AnalysisResult rows under the same job_id.
    # Stage 1 produces one workbook per attempt, so only the newest successful
    # result belongs to the stage's current successful attempt.
    result_file_id = db.scalar(
        select(AnalysisResult.result_file_id)
        .where(
            AnalysisResult.job_id == job.id,
            AnalysisResult.result_type == job.task_type,
            AnalysisResult.status == "succeeded",
            AnalysisResult.result_file_id.is_not(None),
        )
        .order_by(AnalysisResult.id.desc())
        .limit(1)
    )
    return [result_file_id] if result_file_id is not None else []


def category_files(
    db: Session,
    workflow: WorkflowRun,
) -> dict[str, list[StoredFile]]:
    ids_by_category = {
        "classified_dxf": _current_classified_file_ids(db, workflow),
        "processed_dxf": _current_processed_file_ids(db, workflow),
        "source_excel": _source_excel_file_ids(db, workflow),
        "stage1_excel": _stage1_excel_file_ids(db, workflow),
    }
    all_ids = tuple(
        dict.fromkeys(
            file_id
            for category in EXPORT_CATEGORY_ORDER
            for file_id in ids_by_category[category]
            if file_id is not None
        )
    )
    stored_by_id = (
        {
            stored.id: stored
            for stored in db.scalars(
                select(StoredFile).where(
                    StoredFile.id.in_(all_ids),
                    StoredFile.status == "available",
                )
            ).all()
        }
        if all_ids
        else {}
    )
    return {
        category: [
            stored_by_id[file_id]
            for file_id in ids_by_category[category]
            if file_id in stored_by_id
        ]
        for category in EXPORT_CATEGORY_ORDER
    }


def export_preview(db: Session, workflow: WorkflowRun) -> list[dict[str, Any]]:
    files = category_files(db, workflow)
    return [
        {
            "key": category,
            "label": EXPORT_CATEGORY_DEFINITIONS[category]["label"],
            "file_count": len(files[category]),
            "size_bytes": sum(item.size_bytes for item in files[category]),
            "available": bool(files[category]),
        }
        for category in EXPORT_CATEGORY_ORDER
    ]


def _archive_name(original_name: str, category: str) -> str:
    if (
        not original_name
        or original_name in {".", ".."}
        or "/" in original_name
        or "\\" in original_name
        or "\x00" in original_name
        or PurePosixPath(original_name).name != original_name
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_EXPORT_FILENAME_INVALID",
            "登记文件名无法安全写入导出压缩包。",
            {"category": category, "original_name": original_name},
        )
    return f"{EXPORT_CATEGORY_DEFINITIONS[category]['folder']}/{original_name}"


def _build_manifest(
    files: dict[str, list[StoredFile]],
    categories: list[str],
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for category in EXPORT_CATEGORY_ORDER:
        if category not in categories:
            continue
        selected = files[category]
        if not selected:
            raise AppHTTPException(
                409,
                "WORKFLOW_EXPORT_CATEGORY_EMPTY",
                "所选导出类别没有可用文件。",
                {"category": category},
            )
        for stored in selected:
            archive_path = _archive_name(stored.original_name, category)
            path_key = archive_path.casefold()
            if path_key in seen_paths:
                raise AppHTTPException(
                    409,
                    "WORKFLOW_EXPORT_FILENAME_CONFLICT",
                    "两个文件会生成相同的压缩包路径；系统未修改原文件名。",
                    {
                        "category": category,
                        "original_name": stored.original_name,
                    },
                )
            seen_paths.add(path_key)
            manifest.append(
                {
                    "file_id": stored.id,
                    "category": category,
                    "archive_path": archive_path,
                    "bucket": stored.bucket,
                    "storage_key": stored.storage_key,
                    "original_name": stored.original_name,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                }
            )
    return manifest


def create_export(
    db: Session,
    workflow: WorkflowRun,
    *,
    categories: list[str],
    actor_user_id: int,
) -> tuple[WorkflowBatchExport, str]:
    files = category_files(db, workflow)
    manifest = _build_manifest(files, categories)
    token = secrets.token_urlsafe(32)
    row = WorkflowBatchExport(
        export_uid=str(uuid4()),
        workflow_run_id=workflow.id,
        created_by=actor_user_id,
        status="prepared",
        categories_json=[category for category in EXPORT_CATEGORY_ORDER if category in categories],
        manifest_json=manifest,
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        token_expires_at=datetime.now(UTC)
        + timedelta(minutes=settings.workflow_batch_export_ttl_minutes),
        file_count=len(manifest),
        source_size_bytes=sum(int(item["size_bytes"]) for item in manifest),
    )
    db.add(row)
    db.flush()
    return row, token


def export_filename(workflow_id: int) -> str:
    return f"workflow-{workflow_id}-batch-export.zip"


def export_download_path(workflow_id: int, export_uid: str) -> str:
    return f"{settings.api_v1_prefix}/workflows/{workflow_id}/batch-exports/{export_uid}/download"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_export_token(row: WorkflowBatchExport, token: str | None) -> None:
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
            "WORKFLOW_EXPORT_TOKEN_INVALID",
            "本次分批导出的下载凭据无效。",
        )
    if _as_utc(row.token_expires_at) < datetime.now(UTC):
        raise AppHTTPException(
            410,
            "WORKFLOW_EXPORT_TOKEN_EXPIRED",
            "本次分批导出的下载凭据已过期，请重新创建导出。",
        )


def require_export_owner(
    row: WorkflowBatchExport,
    *,
    actor_user_id: int,
    actor_is_admin: bool,
) -> None:
    if row.created_by != actor_user_id and not actor_is_admin:
        raise forbidden("只有创建本次导出的用户或管理员可以管理它。")


def load_export(
    db: Session,
    workflow_id: int,
    export_uid: str,
    *,
    for_update: bool = False,
) -> WorkflowBatchExport:
    statement = select(WorkflowBatchExport).where(
        WorkflowBatchExport.workflow_run_id == workflow_id,
        WorkflowBatchExport.export_uid == export_uid,
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise not_found("WorkflowBatchExport")
    return row


def _manifest_and_file_ids(
    row: WorkflowBatchExport,
) -> tuple[list[dict[str, Any]], tuple[int, ...]]:
    raw_manifest = row.manifest_json
    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise _stale_export()
    manifest: list[dict[str, Any]] = []
    file_ids: list[int] = []
    for item in raw_manifest:
        if not isinstance(item, dict):
            raise _stale_export()
        try:
            file_id = int(item["file_id"])
        except (KeyError, TypeError, ValueError):
            raise _stale_export() from None
        if file_id < 1:
            raise _stale_export()
        manifest.append(item)
        file_ids.append(file_id)
    return manifest, tuple(dict.fromkeys(file_ids))


def _manifest_item_matches(stored: StoredFile, item: dict[str, Any]) -> bool:
    try:
        category = str(item["category"])
        return bool(
            category in EXPORT_CATEGORY_DEFINITIONS
            and stored.id == int(item["file_id"])
            and stored.bucket == item["bucket"]
            and stored.storage_key == item["storage_key"]
            and stored.original_name == item["original_name"]
            and stored.size_bytes == int(item["size_bytes"])
            and stored.sha256 == item["sha256"]
            and item["archive_path"] == _archive_name(stored.original_name, category)
        )
    except (KeyError, TypeError, ValueError):
        return False


def storage_members_for_download(
    db: Session,
    row: WorkflowBatchExport,
) -> list[StorageZipMember]:
    manifest, file_ids = _manifest_and_file_ids(row)
    stored_by_id = (
        {
            item.id: item
            for item in db.scalars(
                select(StoredFile).where(
                    StoredFile.id.in_(file_ids),
                    StoredFile.status == "available",
                )
            ).all()
        }
        if file_ids
        else {}
    )
    members: list[StorageZipMember] = []
    for item in manifest:
        try:
            file_id = int(item["file_id"])
            stored = stored_by_id[file_id]
            if not _manifest_item_matches(stored, item):
                raise _stale_export()
            members.append(
                StorageZipMember(
                    bucket=stored.bucket,
                    storage_key=stored.storage_key,
                    archive_path=str(item["archive_path"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise _stale_export() from None
    if not members or len(members) != row.file_count:
        raise _stale_export()
    return members


def _stale_export() -> AppHTTPException:
    return AppHTTPException(
        409,
        "WORKFLOW_EXPORT_MANIFEST_STALE",
        "导出清单创建后有文件登记发生变化，请重新创建导出。",
    )


def mark_download_result(
    factory: sessionmaker[Session],
    export_uid: str,
    *,
    succeeded: bool,
) -> None:
    with factory.begin() as db:
        row = db.scalar(
            select(WorkflowBatchExport)
            .where(WorkflowBatchExport.export_uid == export_uid)
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
            row.error_code = "WORKFLOW_EXPORT_DOWNLOAD_FAILED"
            row.error_message = "压缩包未完整传输，服务器文件已保留。"


def track_export_stream(
    factory: sessionmaker[Session],
    export_uid: str,
    chunks: Iterable[bytes],
) -> Iterable[bytes]:
    try:
        yield from chunks
    except BaseException:
        mark_download_result(factory, export_uid, succeeded=False)
        raise
    else:
        mark_download_result(factory, export_uid, succeeded=True)


def _preview_files_for_sources(
    db: Session,
    sources: Iterable[StoredFile],
) -> list[StoredFile]:
    batch_names = [
        preview_batch_name(source) for source in sources if source.file_ext.lower() == ".dxf"
    ]
    if not batch_names:
        return []
    return list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name.in_(batch_names),
                StoredFile.file_ext == ".svg",
                StoredFile.status == "available",
            )
        ).all()
    )


def _download_succeeded(db: Session, export_uid: str) -> bool:
    return (
        db.scalar(
            select(FileTransfer.id)
            .where(
                FileTransfer.operation == "workflow_batch_export",
                FileTransfer.batch_ref == export_uid,
                FileTransfer.status == "succeeded",
            )
            .limit(1)
        )
        is not None
    )


def purge_export(
    db: Session,
    workflow: WorkflowRun,
    row: WorkflowBatchExport,
    *,
    actor_user_id: int,
    request_id: str,
) -> tuple[int, int]:
    if row.status == "purged":
        return row.purged_file_count, row.purged_size_bytes
    if row.status != "downloaded" or not _download_succeeded(db, row.export_uid):
        raise AppHTTPException(
            409,
            "WORKFLOW_EXPORT_NOT_DOWNLOADED",
            "只有服务端确认压缩包完整传输后，才允许永久删除服务器文件。",
        )
    if any(stage.status in ACTIVE_STAGE_STATUSES for stage in workflow.stages):
        raise AppHTTPException(
            409,
            "WORKFLOW_EXPORT_PURGE_ACTIVE_STAGE",
            "工作流仍有阶段正在排队或执行，暂不能删除服务器文件。",
        )

    manifest, file_ids = _manifest_and_file_ids(row)
    selected = list(
        db.scalars(
            select(StoredFile)
            .where(
                StoredFile.id.in_(file_ids),
                StoredFile.status == "available",
            )
            .with_for_update()
        ).all()
    )
    if len(selected) != len(file_ids):
        raise _stale_export()
    selected_by_id = {item.id: item for item in selected}
    for item in manifest:
        stored = selected_by_id.get(int(item["file_id"]))
        if stored is None or not _manifest_item_matches(stored, item):
            raise _stale_export()

    previews = _preview_files_for_sources(db, selected)
    targets = list({item.id: item for item in [*selected, *previews]}.values())
    released_bytes = sum(item.size_bytes for item in targets)
    scope_bucket = "multiple"
    scope_key = f"workflow-export/{row.export_uid}"
    transfer, durable = prepare_destructive_transfer(
        db,
        TransferSpec(
            direction="internal",
            operation="workflow_export_purge",
            actor_user_id=actor_user_id,
            request_id=request_id,
            idempotency_key=request_id,
            batch_ref=row.export_uid,
            bucket=scope_bucket,
            storage_key=scope_key,
            original_name=export_filename(workflow.id),
            expected_bytes=released_bytes,
        ),
    )

    storage = storage_factory.get_storage_backend()
    deleted_bytes = 0
    try:
        for stored in targets:
            storage.delete_object(stored.bucket, stored.storage_key)
            deleted_bytes += stored.size_bytes
    except Exception as exc:
        if durable:
            settle_transfer(
                session_factory_for(db),
                transfer.transfer_uid,
                status="compensation_required" if deleted_bytes else "failed",
                transferred_bytes=deleted_bytes,
                error_code="WORKFLOW_EXPORT_PURGE_PARTIAL"
                if deleted_bytes
                else "STORAGE_DELETE_FAILED",
                error_message="分批导出永久清理未能删除全部存储对象。",
            )
        raise AppHTTPException(
            503,
            "WORKFLOW_EXPORT_PURGE_FAILED",
            "永久清理未能删除全部服务器对象；请核对流水后重试。",
            {"deleted_bytes": deleted_bytes},
        ) from exc

    if durable:
        # Register before any metadata mutation so a later flush, audit or
        # commit failure settles the irreversible deletion as compensation-required.
        register_pending_destructive_transfer(
            db,
            transfer.transfer_uid,
            transferred_bytes=released_bytes,
        )

    now = utcnow()
    for stored in targets:
        stored.status = "deleted"
        stored.deleted_at = now
        stored.purged_at = now
    if file_ids:
        db.execute(
            delete(WorkflowArtifact).where(
                WorkflowArtifact.workflow_run_id == workflow.id,
                WorkflowArtifact.file_id.in_(file_ids),
            )
        )

    row.status = "purged"
    row.purged_at = now
    row.purged_file_count = len(targets)
    row.purged_size_bytes = released_bytes
    row.manifest_json = []
    row.token_digest = None
    row.error_code = None
    row.error_message = None
    if not durable:
        complete_transfer_in_transaction(
            db,
            transfer.transfer_uid,
            file_id=None,
            bucket=scope_bucket,
            storage_key=scope_key,
            original_name=export_filename(workflow.id),
            transferred_bytes=released_bytes,
        )
    db.flush()
    return len(targets), released_bytes


__all__ = [
    "EXPORT_CATEGORY_DEFINITIONS",
    "EXPORT_CATEGORY_ORDER",
    "EXPORT_COOKIE_NAME",
    "category_files",
    "create_export",
    "export_download_path",
    "export_filename",
    "export_preview",
    "load_export",
    "mark_download_result",
    "purge_export",
    "require_export_owner",
    "require_export_token",
    "storage_members_for_download",
    "track_export_stream",
]
