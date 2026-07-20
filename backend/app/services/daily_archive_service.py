from __future__ import annotations

import base64
import hashlib
import hmac
import json
import zipfile
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

import app.modules.files.interface as storage_service
from app.models.daily_archive import DailyArchiveRun
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
from app.platform.config.settings import settings
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageError
from app.schemas.data_admin_schema import DailyArchivePreview, DailyArchiveRunRead

DAILY_ARCHIVE_PREFIX = "daily-archives/"
DAILY_ARCHIVE_ACTIVE = {"queued", "running"}
DAILY_ARCHIVE_TERMINAL = {"succeeded", "failed", "cancelled"}
_TOKEN_KIND = "daily-archive-preview-v1"


def _business_zone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.business_timezone)
    except ZoneInfoNotFoundError as exc:
        raise AppHTTPException(
            500,
            "BUSINESS_TIMEZONE_INVALID",
            "Configured business timezone is not available.",
        ) from exc


def current_business_date() -> date:
    return datetime.now(_business_zone()).date()


def _day_window(archive_date: date) -> tuple[datetime, datetime]:
    zone = _business_zone()
    start = datetime.combine(archive_date, time.min, tzinfo=zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _canonical_files(rows: list[StoredFile]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "bucket": row.bucket,
            "storage_key": row.storage_key,
            "original_name": row.original_name,
            "file_ext": row.file_ext,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "created_at": _utc_iso(row.created_at),
        }
        for row in sorted(rows, key=lambda item: item.id)
    ]


def _manifest_sha256(rows: list[StoredFile]) -> str:
    payload = json.dumps(
        _canonical_files(rows),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _urlsafe_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _urlsafe_decode(payload: str) -> bytes:
    return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))


def _sign_preview(payload: dict[str, Any]) -> str:
    encoded = _urlsafe_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def _decode_preview(token: str) -> dict[str, Any]:
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(
            settings.jwt_secret_key.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _urlsafe_decode(supplied)):
            raise ValueError("signature mismatch")
        payload = json.loads(_urlsafe_decode(encoded))
        if payload.get("kind") != _TOKEN_KIND:
            raise ValueError("wrong token kind")
        if int(payload["expires_at"]) < int(datetime.now(UTC).timestamp()):
            raise ValueError("expired")
        file_ids = payload.get("file_ids")
        if not isinstance(file_ids, list) or not all(
            isinstance(file_id, int) and file_id > 0 for file_id in file_ids
        ):
            raise ValueError("invalid file ids")
        if len(file_ids) > settings.daily_archive_max_files:
            raise ValueError("too many files")
        date.fromisoformat(str(payload["archive_date"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, base64.binascii.Error) as exc:
        raise AppHTTPException(
            422,
            "DAILY_ARCHIVE_PREVIEW_INVALID",
            "Daily archive preview is invalid or expired; run preview again.",
        ) from exc
    return payload


def _source_rows_for_day(
    db: Session,
    *,
    archive_date: date,
    scope_bucket: str | None,
) -> tuple[list[StoredFile], int, datetime, datetime]:
    start, end = _day_window(archive_date)
    base_filters = [
        StoredFile.status == "available",
        StoredFile.created_at >= start,
        StoredFile.created_at < end,
    ]
    if scope_bucket is not None:
        base_filters.append(StoredFile.bucket == scope_bucket)
    archive_output = and_(
        StoredFile.bucket == settings.minio_bucket_reports,
        StoredFile.storage_key.like(f"{DAILY_ARCHIVE_PREFIX}%"),
    )
    rows = db.scalars(
        select(StoredFile)
        .where(*base_filters, ~archive_output)
        .order_by(StoredFile.id)
    ).all()
    excluded = len(
        db.scalars(select(StoredFile.id).where(*base_filters, archive_output)).all()
    )
    return list(rows), excluded, start, end


def preview_daily_archive(
    db: Session,
    *,
    archive_date: date | None,
    scope_bucket: str | None,
) -> DailyArchivePreview:
    archive_date = archive_date or current_business_date()
    if scope_bucket is not None and scope_bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    rows, excluded, start, end = _source_rows_for_day(
        db,
        archive_date=archive_date,
        scope_bucket=scope_bucket,
    )
    total_bytes = sum(row.size_bytes for row in rows)
    max_bytes = settings.daily_archive_max_source_gb * 1024**3
    block_reason: str | None = None
    if not rows:
        block_reason = "所选日期和范围内没有可归档文件"
    elif len(rows) > settings.daily_archive_max_files:
        block_reason = f"文件数超过单次上限 {settings.daily_archive_max_files}"
    elif total_bytes > max_bytes:
        block_reason = f"源文件总量超过单次上限 {settings.daily_archive_max_source_gb} GiB"
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.daily_archive_preview_ttl_minutes
    )
    source_hash = _manifest_sha256(rows)
    token = _sign_preview(
        {
            "kind": _TOKEN_KIND,
            "archive_date": archive_date.isoformat(),
            "timezone": settings.business_timezone,
            "scope_bucket": scope_bucket,
            "file_ids": [row.id for row in rows],
            "source_manifest_sha256": source_hash,
            "expires_at": int(expires_at.timestamp()),
        }
    )
    return DailyArchivePreview(
        archive_date=archive_date,
        timezone=settings.business_timezone,
        scope_bucket=scope_bucket,
        window_start=start,
        window_end=end,
        file_count=len(rows),
        total_bytes=total_bytes,
        excluded_archive_files=excluded,
        bucket_counts=dict(sorted(Counter(row.bucket for row in rows).items())),
        format_counts=dict(sorted(Counter(row.file_ext for row in rows).items())),
        source_manifest_sha256=source_hash,
        can_archive=block_reason is None,
        block_reason=block_reason,
        expires_at=expires_at,
        preview_token=token,
    )


def _scope_key(scope_bucket: str | None) -> str:
    return scope_bucket or "all-configured-buckets"


def prepare_daily_archive_run(
    db: Session,
    *,
    actor_user_id: int,
    preview_token: str,
    idempotency_key: str,
) -> tuple[DailyArchiveRun, bool]:
    payload = _decode_preview(preview_token)
    file_ids = [int(value) for value in payload["file_ids"]]
    if not file_ids:
        raise AppHTTPException(
            422,
            "DAILY_ARCHIVE_EMPTY",
            "No files are available in this preview.",
        )
    archive_date = date.fromisoformat(payload["archive_date"])
    scope_bucket = payload.get("scope_bucket")
    if scope_bucket is not None and scope_bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    if payload.get("timezone") != settings.business_timezone:
        raise AppHTTPException(
            409,
            "DAILY_ARCHIVE_TIMEZONE_CHANGED",
            "Business timezone changed after preview; run preview again.",
        )
    scope_key = _scope_key(scope_bucket)
    source_hash = str(payload["source_manifest_sha256"])

    existing = db.scalar(
        select(DailyArchiveRun).where(
            DailyArchiveRun.actor_user_id == actor_user_id,
            DailyArchiveRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing, True
    reusable = db.scalar(
        select(DailyArchiveRun)
        .where(
            DailyArchiveRun.archive_date == archive_date,
            DailyArchiveRun.scope_key == scope_key,
            DailyArchiveRun.source_manifest_sha256 == source_hash,
            DailyArchiveRun.status == "succeeded",
        )
        .order_by(DailyArchiveRun.id.desc())
        .limit(1)
    )
    if reusable is not None:
        return reusable, True
    active = db.scalar(
        select(DailyArchiveRun)
        .where(
            DailyArchiveRun.archive_date == archive_date,
            DailyArchiveRun.scope_key == scope_key,
            DailyArchiveRun.status.in_(DAILY_ARCHIVE_ACTIVE),
        )
        .order_by(DailyArchiveRun.id.desc())
        .limit(1)
    )
    if active is not None:
        return active, True

    rows = db.scalars(
        select(StoredFile).where(StoredFile.id.in_(file_ids)).order_by(StoredFile.id)
    ).all()
    rows = list(rows)
    if len(rows) != len(file_ids) or any(row.status != "available" for row in rows):
        raise AppHTTPException(
            409,
            "DAILY_ARCHIVE_SOURCE_CHANGED",
            "Source files changed after preview; run preview again.",
        )
    if _manifest_sha256(rows) != source_hash:
        raise AppHTTPException(
            409,
            "DAILY_ARCHIVE_SOURCE_CHANGED",
            "Source files changed after preview; run preview again.",
        )
    row = DailyArchiveRun(
        archive_date=archive_date,
        timezone=settings.business_timezone,
        scope_bucket=scope_bucket,
        scope_key=scope_key,
        status="queued",
        actor_user_id=actor_user_id,
        source_file_ids_json=file_ids,
        source_manifest_sha256=source_hash,
        file_count=len(rows),
        total_bytes=sum(item.size_bytes for item in rows),
        bucket_counts_json=dict(sorted(Counter(item.bucket for item in rows).items())),
        format_counts_json=dict(sorted(Counter(item.file_ext for item in rows).items())),
        idempotency_key=idempotency_key,
    )
    db.add(row)
    db.flush()
    return row, False


def daily_archive_run_data(
    row: DailyArchiveRun,
    *,
    reused: bool = False,
) -> DailyArchiveRunRead:
    return DailyArchiveRunRead(
        id=row.id,
        archive_date=row.archive_date,
        timezone=row.timezone,
        scope_bucket=row.scope_bucket,
        status=row.status,
        actor_user_id=row.actor_user_id,
        source_manifest_sha256=row.source_manifest_sha256,
        file_count=row.file_count,
        total_bytes=row.total_bytes,
        bucket_counts=row.bucket_counts,
        format_counts=row.format_counts,
        task_id=row.task_id,
        archive_file_id=row.archive_file_id,
        manifest_file_id=row.manifest_file_id,
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        reused=reused,
    )


def _safe_archive_name(row: StoredFile) -> str:
    safe = storage_service.sanitize_filename(row.original_name)
    return f"{row.bucket}/{row.id}_{safe}"


def _build_archive_files(
    run: DailyArchiveRun,
    rows: list[StoredFile],
) -> tuple[Path, bytes]:
    canonical = _canonical_files(rows)
    for item, row in zip(canonical, sorted(rows, key=lambda value: value.id), strict=True):
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
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for item, row in zip(canonical, sorted(rows, key=lambda value: value.id), strict=True):
                with archive.open(item["archive_path"], "w", force_zip64=True) as target:
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
            original_name=f"daily-archive-{run.archive_date.isoformat()}-manifest.json",
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
            storage_key=f"{base}/daily-archive-{run.archive_date.isoformat()}.zip",
            original_name=f"daily-archive-{run.archive_date.isoformat()}.zip",
            expected_bytes=archive_path.stat().st_size,
        ),
    ]


def _prepare_sqlite_transfer(db: Session, spec: TransferSpec) -> str:
    snapshot = prepare_transfer_in_transaction(db, spec)
    row = db.scalar(
        select(FileTransfer).where(FileTransfer.transfer_uid == snapshot.transfer_uid)
    )
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
        specs = _output_specs(run, archive_path=archive_path, manifest_bytes=manifest_bytes)
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
                    error_message="Daily archive output transfer could not be prepared.",
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
            select(DailyArchiveRun)
            .where(DailyArchiveRun.id == run_id)
            .with_for_update()
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
            rows = db.scalars(
                select(StoredFile)
                .where(StoredFile.id.in_(run.source_file_ids))
                .order_by(StoredFile.id)
            ).all()
            rows = list(rows)
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
