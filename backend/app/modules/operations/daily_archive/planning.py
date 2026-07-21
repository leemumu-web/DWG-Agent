from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile
from app.modules.operations.daily_archive.models import DailyArchiveRun
from app.modules.operations.daily_archive.schemas import DailyArchivePreview
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException

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
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        base64.binascii.Error,
    ) as exc:
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
        select(StoredFile).where(*base_filters, ~archive_output).order_by(StoredFile.id)
    ).all()
    excluded = len(db.scalars(select(StoredFile.id).where(*base_filters, archive_output)).all())
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
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.daily_archive_preview_ttl_minutes)
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

    rows = list(
        db.scalars(
            select(StoredFile).where(StoredFile.id.in_(file_ids)).order_by(StoredFile.id)
        ).all()
    )
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


__all__ = [
    "DAILY_ARCHIVE_ACTIVE",
    "DAILY_ARCHIVE_PREFIX",
    "DAILY_ARCHIVE_TERMINAL",
    "current_business_date",
    "prepare_daily_archive_run",
    "preview_daily_archive",
]
