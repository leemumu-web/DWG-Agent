from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember
from app.models.user import User
from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.platform.http.exceptions import forbidden, not_found


def user_role_codes(user: User) -> set[str]:
    return {role.code for role in user.roles}


def has_global_project_access(user: User) -> bool:
    return bool({ROLE_SUPER_ADMIN, ROLE_ADMIN}.intersection(user_role_codes(user)))


def is_admin(user: User) -> bool:
    """Return True if the user holds admin or super_admin global permissions (§8.3)."""
    return has_global_project_access(user)


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
