from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, is_admin
from app.modules.projects.models.project import Project, ProjectMember
from app.platform.http.exceptions import forbidden, not_found


def has_global_project_access(user: User) -> bool:
    return is_admin(user)


def get_project_membership(db: Session, user: User, project_id: int) -> ProjectMember | None:
    return db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )


def require_active_project(db: Session, project_id: int) -> None:
    """Raise 404 if the project does not exist or has been soft-deleted."""
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")


def require_project_member(db: Session, user: User, project_id: int) -> ProjectMember | None:
    if has_global_project_access(user):
        return None
    require_active_project(db, project_id)
    member = get_project_membership(db, user, project_id)
    if not member:
        raise forbidden("Project membership is required.")
    return member


def require_project_role(
    db: Session,
    user: User,
    project_id: int,
    allowed_project_roles: set[str],
) -> ProjectMember | None:
    if has_global_project_access(user):
        return None
    member = require_project_member(db, user, project_id)
    if member and member.project_role in allowed_project_roles:
        return member
    raise forbidden("Project role is not allowed for this action.")
