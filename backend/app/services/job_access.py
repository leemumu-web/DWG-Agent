from __future__ import annotations

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.job import Job
from app.modules.identity.interface import User
from app.modules.projects.interface import (
    ProjectMember,
    has_global_project_access,
    require_project_member,
    require_project_role,
)
from app.platform.http.exceptions import forbidden

PROJECT_JOB_WRITE_ROLES = {"project_owner", "project_engineer"}


def job_read_filter(user: User) -> ColumnElement[bool]:
    """Return the SQL predicate for jobs visible to a non-admin user."""
    project_membership = exists(
        select(ProjectMember.id).where(
            ProjectMember.project_id == Job.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    return or_(
        and_(Job.project_id.is_(None), Job.created_by == user.id),
        and_(Job.project_id.is_not(None), project_membership),
    )


def require_job_read_access(db: Session, user: User, job: Job) -> None:
    if has_global_project_access(user):
        return
    if job.project_id is not None:
        require_project_member(db, user, job.project_id)
        return
    if job.created_by != user.id:
        raise forbidden("Job access is restricted to its creator.")


def require_job_write_access(db: Session, user: User, job: Job) -> None:
    if has_global_project_access(user):
        return
    if job.project_id is not None:
        require_project_role(db, user, job.project_id, PROJECT_JOB_WRITE_ROLES)
        return
    if job.created_by != user.id:
        raise forbidden("Only the job creator or an administrator can modify this job.")
