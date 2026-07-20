"""Authorization and owned-resource lookup for Excel Final endpoints."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.excel_processing.models import ExcelFinalBatch
from app.modules.files.interface import StoredFile
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import Job, require_job_read_access
from app.modules.projects.interface import has_global_project_access
from app.platform.config.constants import TASK_EXCEL_FINAL
from app.platform.http.exceptions import forbidden, not_found


def require_input_file_access(current_user: CurrentUser, stored: StoredFile) -> None:
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return
    raise forbidden("Only the file uploader or an administrator can process this file.")


def get_excel_job(db: Session, current_user: CurrentUser, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job or job.task_type != TASK_EXCEL_FINAL:
        raise not_found("Excel Final job")
    require_job_read_access(db, current_user, job)
    return job


def get_accessible_batch(
    db: Session,
    current_user: CurrentUser,
    batch_id: int,
) -> ExcelFinalBatch:
    batch = db.get(ExcelFinalBatch, batch_id)
    if not batch:
        raise not_found("Batch")
    job = db.get(Job, batch.job_id)
    if not job or job.task_type != TASK_EXCEL_FINAL:
        raise not_found("Excel Final job")
    require_job_read_access(db, current_user, job)
    return batch


__all__ = ["get_accessible_batch", "get_excel_job", "require_input_file_access"]
