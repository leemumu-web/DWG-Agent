"""Project-scoped access rules shared through projects/interface.py.

Calling contract for every cross-module consumer (files/jobs/workflows):

- Global bypass: admin/super_admin pass every check and the helpers return
  ``None`` for the membership row — ``None`` means "authorized without a
  membership row", not "not a member".
- ``require_active_project`` raises 404 for a missing or soft-deleted
  project without leaking whether it exists; membership failures raise 403.
- ``require_project_role`` raises 403 when the member's role is not in
  ``allowed_project_roles``; it also requires an active project first.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, is_admin
from app.modules.projects.models.project import Project, ProjectMember
from app.platform.http.exceptions import forbidden, not_found


def has_global_project_access(user: User) -> bool:
    """Whether the user bypasses project-scoped rules (admin/super_admin)."""
    return is_admin(user)


def get_project_membership(db: Session, user: User, project_id: int) -> ProjectMember | None:
    """Return the user's membership row for a project, or None."""
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
    """Require membership (or global admin); return the membership row.

    Admin/super_admin return ``None`` (authorized without a row); missing or
    soft-deleted project → 404; non-member → 403.
    """
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
    """Require an allowed project role (or global admin).

    Admin/super_admin return ``None``; the member's ``project_role`` must be
    in ``allowed_project_roles``, otherwise 403.
    """
    if has_global_project_access(user):
        return None
    member = require_project_member(db, user, project_id)
    if member and member.project_role in allowed_project_roles:
        return member
    raise forbidden("Project role is not allowed for this action.")
