from __future__ import annotations

from app.modules.operations.daily_archive.models import DailyArchiveRun
from app.modules.operations.daily_archive.schemas import DailyArchiveRunRead


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


__all__ = ["daily_archive_run_data"]
