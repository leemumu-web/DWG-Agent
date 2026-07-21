from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class DailyArchivePreview(BaseModel):
    archive_date: date
    timezone: str
    scope_bucket: str | None
    window_start: datetime
    window_end: datetime
    file_count: int
    total_bytes: int
    excluded_archive_files: int
    bucket_counts: dict[str, int]
    format_counts: dict[str, int]
    source_manifest_sha256: str
    can_archive: bool
    block_reason: str | None
    expires_at: datetime
    preview_token: str


class DailyArchiveRunRead(BaseModel):
    id: int
    archive_date: date
    timezone: str
    scope_bucket: str | None
    status: str
    actor_user_id: int
    source_manifest_sha256: str
    file_count: int
    total_bytes: int
    bucket_counts: dict[str, int]
    format_counts: dict[str, int]
    task_id: str | None
    archive_file_id: int | None
    manifest_file_id: int | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    reused: bool = False
