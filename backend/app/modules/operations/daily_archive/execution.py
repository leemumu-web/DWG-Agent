from __future__ import annotations

import json
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.modules.files.interface as storage_service
from app.modules.files.interface import (
    FileTransfer,
    StoredFile,
    TransferSpec,
    begin_transfer,
    complete_transfer_in_transaction,
    mark_transfer_in_progress,
    prepare_transfer_in_transaction,
    settle_transfer,
)
from app.modules.operations.daily_archive.models import DailyArchiveRun
from app.modules.operations.daily_archive.planning import (
    DAILY_ARCHIVE_TERMINAL,
    _canonical_files,
    _manifest_sha256,
    _utc_iso,
)
from app.platform.config.settings import settings
from app.platform.database.mixins import utcnow
from app.platform.storage.base import StorageError


def _safe_archive_name(row: StoredFile) -> str:
    safe = storage_service.sanitize_filename(row.original_name)
    return f"{row.bucket}/{row.id}_{safe}"


def _build_archive_files(
    run: DailyArchiveRun,
    rows: list[StoredFile],
) -> tuple[Path, bytes]:
    canonical = _canonical_files(rows)
    for item, row in zip(
        canonical,
        sorted(rows, key=lambda value: value.id),
        strict=True,
    ):
        item["archive_path"] = _safe_archive_name(row)
    manifest = {
        "schema": "dwg-agent.daily-archive/v1",
        "archive_date": run.archive_date.isoformat(),
        "timezone": run.timezone,
        "scope_bucket": run.scope_bucket,
        "source_manifest_sha256": run.source_manifest_sha256,
        "file_count": run.file_count,
        "total_bytes": run.total_bytes,
        "created_at": _utc_iso(utcnow()),
        "files": canonical,
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    tmp = NamedTemporaryFile(suffix=".zip", delete=False)
    path = Path(tmp.name)
    tmp.close()
    storage = storage_service.get_storage_backend()
    try:
        with zipfile.ZipFile(
            path,
            "w",
            zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for item, row in zip(
                canonical,
                sorted(rows, key=lambda value: value.id),
                strict=True,
            ):
                with archive.open(
                    item["archive_path"],
                    "w",
                    force_zip64=True,
                ) as target:
                    for chunk in storage.iter_file(row.bucket, row.storage_key):
                        target.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, manifest_bytes


def _output_specs(
    run: DailyArchiveRun,
    *,
    archive_path: Path,
    manifest_bytes: bytes,
) -> list[TransferSpec]:
    base = f"daily-archives/{run.archive_date.isoformat()}/run-{run.id}"
    batch_ref = f"daily-archive:{run.id}"
    return [
        TransferSpec(
            direction="internal",
            operation="daily_archive_manifest",
            actor_user_id=run.actor_user_id,
            request_id=f"daily-archive:{run.id}:manifest",
            idempotency_key=f"daily-archive:{run.id}:manifest",
            batch_ref=batch_ref,
            bucket=settings.minio_bucket_reports,
            storage_key=f"{base}/manifest.json",
            original_name=(f"daily-archive-{run.archive_date.isoformat()}-manifest.json"),
            expected_bytes=len(manifest_bytes),
        ),
        TransferSpec(
            direction="internal",
            operation="daily_archive",
            actor_user_id=run.actor_user_id,
            request_id=f"daily-archive:{run.id}:zip",
            idempotency_key=f"daily-archive:{run.id}:zip",
            batch_ref=batch_ref,
            bucket=settings.minio_bucket_reports,
            storage_key=(f"{base}/daily-archive-{run.archive_date.isoformat()}.zip"),
            original_name=f"daily-archive-{run.archive_date.isoformat()}.zip",
            expected_bytes=archive_path.stat().st_size,
        ),
    ]


def _prepare_sqlite_transfer(db: Session, spec: TransferSpec) -> str:
    snapshot = prepare_transfer_in_transaction(db, spec)
    row = db.scalar(select(FileTransfer).where(FileTransfer.transfer_uid == snapshot.transfer_uid))
    assert row is not None
    row.status = "in_progress"
    row.started_at = row.started_at or utcnow()
    return row.transfer_uid


def _persist_archive_outputs(
    run_id: int,
    *,
    factory: sessionmaker[Session],
    archive_path: Path,
    manifest_bytes: bytes,
) -> None:
    with factory() as probe:
        run = probe.get(DailyArchiveRun, run_id)
        if run is None:
            raise RuntimeError("Daily archive run disappeared.")
        specs = _output_specs(
            run,
            archive_path=archive_path,
            manifest_bytes=manifest_bytes,
        )
        dialect = probe.get_bind().dialect.name

    transfer_uids: list[str] = []
    if dialect != "sqlite":
        try:
            for spec in specs:
                snapshot = begin_transfer(factory, spec)
                mark_transfer_in_progress(
                    factory,
                    snapshot.transfer_uid,
                    bucket=spec.bucket or "",
                    storage_key=spec.storage_key or "",
                    expected_bytes=spec.expected_bytes or 0,
                )
                transfer_uids.append(snapshot.transfer_uid)
        except Exception:
            for transfer_uid in transfer_uids:
                settle_transfer(
                    factory,
                    transfer_uid,
                    status="failed",
                    transferred_bytes=0,
                    error_code="DAILY_ARCHIVE_TRANSFER_PREPARE_FAILED",
                    error_message=("Daily archive output transfer could not be prepared."),
                )
            raise

    try:
        with factory.begin() as db:
            run = db.get(DailyArchiveRun, run_id)
            if run is None or run.status != "running":
                raise RuntimeError("Daily archive run is no longer active.")
            if dialect == "sqlite":
                transfer_uids = [_prepare_sqlite_transfer(db, spec) for spec in specs]
            manifest_spec, archive_spec = specs
            manifest_file = storage_service.save_bytes_as_file(
                db,
                bucket=manifest_spec.bucket or settings.minio_bucket_reports,
                storage_key=manifest_spec.storage_key or "",
                original_name=manifest_spec.original_name or "manifest.json",
                file_ext=".json",
                content_type="application/json",
                payload=manifest_bytes,
                uploaded_by=run.actor_user_id,
                batch_name=manifest_spec.batch_ref,
                transfer_uid=transfer_uids[0],
                transfer_operation="daily_archive_manifest",
            )
            complete_transfer_in_transaction(
                db,
                transfer_uids[0],
                file_id=manifest_file.id,
                bucket=manifest_file.bucket,
                storage_key=manifest_file.storage_key,
                original_name=manifest_file.original_name,
                transferred_bytes=manifest_file.size_bytes,
            )
            archive_file = storage_service.save_path_as_file(
                db,
                bucket=archive_spec.bucket or settings.minio_bucket_reports,
                storage_key=archive_spec.storage_key or "",
                original_name=archive_spec.original_name or "daily-archive.zip",
                file_ext=".zip",
                content_type="application/zip",
                source_path=archive_path,
                uploaded_by=run.actor_user_id,
                batch_name=archive_spec.batch_ref,
                transfer_uid=transfer_uids[1],
                transfer_operation="daily_archive",
            )
            complete_transfer_in_transaction(
                db,
                transfer_uids[1],
                file_id=archive_file.id,
                bucket=archive_file.bucket,
                storage_key=archive_file.storage_key,
                original_name=archive_file.original_name,
                transferred_bytes=archive_file.size_bytes,
            )
            run.manifest_file_id = manifest_file.id
            run.archive_file_id = archive_file.id
            run.status = "succeeded"
            run.error_code = None
            run.error_message = None
            run.finished_at = utcnow()
    except Exception:
        for transfer_uid in transfer_uids:
            settle_transfer(
                factory,
                transfer_uid,
                status="failed",
                transferred_bytes=0,
                error_code="DAILY_ARCHIVE_OUTPUT_FAILED",
                error_message="Daily archive output could not be committed.",
            )
        raise


def execute_daily_archive_run(
    run_id: int,
    *,
    factory: sessionmaker[Session],
) -> None:
    archive_path: Path | None = None
    with factory.begin() as db:
        run = db.scalar(
            select(DailyArchiveRun).where(DailyArchiveRun.id == run_id).with_for_update()
        )
        if run is None or run.status in DAILY_ARCHIVE_TERMINAL:
            return
        if run.status != "queued":
            return
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        run.error_code = None
        run.error_message = None
    try:
        with factory() as db:
            run = db.get(DailyArchiveRun, run_id)
            if run is None:
                return
            rows = list(
                db.scalars(
                    select(StoredFile)
                    .where(StoredFile.id.in_(run.source_file_ids))
                    .order_by(StoredFile.id)
                ).all()
            )
            if (
                len(rows) != run.file_count
                or any(row.status != "available" for row in rows)
                or _manifest_sha256(rows) != run.source_manifest_sha256
            ):
                raise RuntimeError("Frozen daily archive sources changed before execution.")
            archive_path, manifest_bytes = _build_archive_files(run, rows)
        _persist_archive_outputs(
            run_id,
            factory=factory,
            archive_path=archive_path,
            manifest_bytes=manifest_bytes,
        )
    except Exception as exc:
        with factory.begin() as db:
            run = db.get(DailyArchiveRun, run_id)
            if run is not None and run.status not in DAILY_ARCHIVE_TERMINAL:
                run.status = "failed"
                run.error_code = (
                    "DAILY_ARCHIVE_STORAGE_READ_FAILED"
                    if isinstance(exc, StorageError)
                    else "DAILY_ARCHIVE_EXECUTION_FAILED"
                )
                run.error_message = "归档未完成；源文件或对象存储在执行期间发生变化，请重新预检。"
                run.finished_at = utcnow()
        raise
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)


__all__ = ["execute_daily_archive_run"]
