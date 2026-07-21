"""Project-aware access rules for registered files."""

from __future__ import annotations

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.modules.files.models import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.projects.interface import (
    Drawing,
    DrawingVersion,
    Project,
    ProjectMember,
    get_project_membership,
    has_global_project_access,
)
from app.platform.http.exceptions import AppHTTPException, forbidden, not_found


def _file_project_association_exists(
    *,
    active_only: bool,
    member_user_id: int | None = None,
) -> ColumnElement[bool]:
    drawing_conditions = [
        DrawingVersion.file_id == StoredFile.id,
        Drawing.status != "deleted",
    ]
    result_conditions = [
        AnalysisResult.result_file_id == StoredFile.id,
        Job.project_id.is_not(None),
    ]
    if active_only:
        drawing_conditions.append(Project.status != "deleted")
        result_conditions.append(Project.status != "deleted")

    drawing_stmt = (
        select(1)
        .select_from(DrawingVersion)
        .join(Drawing, Drawing.id == DrawingVersion.drawing_id)
        .join(Project, Project.id == Drawing.project_id)
    )
    result_stmt = (
        select(1)
        .select_from(AnalysisResult)
        .join(Job, Job.id == AnalysisResult.job_id)
        .join(Project, Project.id == Job.project_id)
    )
    if member_user_id is not None:
        drawing_stmt = drawing_stmt.join(ProjectMember, ProjectMember.project_id == Project.id)
        result_stmt = result_stmt.join(ProjectMember, ProjectMember.project_id == Project.id)
        drawing_conditions.append(ProjectMember.user_id == member_user_id)
        result_conditions.append(ProjectMember.user_id == member_user_id)

    return or_(
        exists(drawing_stmt.where(*drawing_conditions)),
        exists(result_stmt.where(*result_conditions)),
    )


def file_list_access_filter(current_user: User) -> ColumnElement[bool]:
    """Mirror single-file access rules as one SQL predicate for list endpoints."""
    any_project = _file_project_association_exists(active_only=False)
    active_project = _file_project_association_exists(active_only=True)
    not_orphaned_by_project_deletion = or_(~any_project, active_project)

    if has_global_project_access(current_user):
        return not_orphaned_by_project_deletion

    active_membership = _file_project_association_exists(
        active_only=True,
        member_user_id=current_user.id,
    )
    return and_(
        not_orphaned_by_project_deletion,
        or_(StoredFile.uploaded_by == current_user.id, active_membership),
    )


def file_project_ids(
    db: Session,
    file_id: int,
    *,
    include_deleted: bool = False,
) -> set[int]:
    drawing_stmt = (
        select(Drawing.project_id)
        .join(DrawingVersion, DrawingVersion.drawing_id == Drawing.id)
        .join(Project, Project.id == Drawing.project_id)
        .where(
            DrawingVersion.file_id == file_id,
            Drawing.status != "deleted",
        )
    )
    if not include_deleted:
        drawing_stmt = drawing_stmt.where(Project.status != "deleted")
    drawing_project_ids = db.scalars(drawing_stmt).all()

    result_stmt = (
        select(Job.project_id)
        .join(AnalysisResult, AnalysisResult.job_id == Job.id)
        .join(Project, Project.id == Job.project_id)
        .where(
            AnalysisResult.result_file_id == file_id,
            Job.project_id.is_not(None),
        )
    )
    if not include_deleted:
        result_stmt = result_stmt.where(Project.status != "deleted")
    result_project_ids = db.scalars(result_stmt).all()

    return {
        project_id
        for project_id in (*drawing_project_ids, *result_project_ids)
        if project_id is not None
    }


def can_read_file(db: Session, current_user: User, stored: StoredFile) -> bool:
    active_project_ids = file_project_ids(db, stored.id, include_deleted=False)
    if not active_project_ids and file_project_ids(db, stored.id, include_deleted=True):
        return False
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return True
    return any(
        get_project_membership(db, current_user, project_id) for project_id in active_project_ids
    )


def require_file_read_access(db: Session, current_user: User, stored: StoredFile) -> None:
    active_ids = file_project_ids(db, stored.id, include_deleted=False)
    all_ids = file_project_ids(db, stored.id, include_deleted=True)
    if not active_ids and all_ids:
        raise not_found("File")
    if not can_read_file(db, current_user, stored):
        raise forbidden("File access is restricted.")


def require_file_delete_access(db: Session, current_user: User, stored: StoredFile) -> None:
    # Resolve lazily: workflow registration depends on files.interface, so a
    # top-level reverse import would make both public boundaries order-sensitive.
    from app.modules.workflows.interface import find_frozen_input_reference

    frozen_reference = find_frozen_input_reference(db, stored.id, for_update=True)
    if frozen_reference is not None:
        raise AppHTTPException(
            409,
            "FILE_REFERENCED_BY_FROZEN_INPUT",
            "A file in a frozen production input manifest cannot be deleted.",
            {
                "workflow_id": frozen_reference.workflow_id,
                "input_batch_id": frozen_reference.input_batch_id,
            },
        )

    active_ids = file_project_ids(db, stored.id, include_deleted=False)
    all_ids = file_project_ids(db, stored.id, include_deleted=True)
    if not active_ids and all_ids:
        raise not_found("File")
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return
    raise forbidden("Only the uploader or an administrator can delete this file.")


__all__ = [
    "can_read_file",
    "file_list_access_filter",
    "file_project_ids",
    "require_file_delete_access",
    "require_file_read_access",
]
