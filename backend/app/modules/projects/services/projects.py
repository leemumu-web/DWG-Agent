from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.projects.models.project import Project, ProjectMember
from app.modules.projects.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberUpdate,
)
from app.platform.http.exceptions import AppHTTPException, not_found


def create_project(db: Session, payload: ProjectCreate, owner_id: int) -> Project:
    """Create a project and auto-assign the creator as project_owner."""
    from sqlalchemy.exc import IntegrityError

    if db.scalar(select(Project).where(Project.code == payload.code)):
        raise AppHTTPException(409, "PROJECT_CODE_EXISTS", "Project code already exists.")
    project = Project(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        owner_id=owner_id,
        status="active",
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppHTTPException(
            409, "PROJECT_CODE_EXISTS", "Project code already exists (concurrent creation)."
        ) from None
    db.add(
        ProjectMember(project_id=project.id, user_id=owner_id, project_role="project_owner")
    )
    return project


def add_project_member(
    db: Session, project_id: int, payload: ProjectMemberCreate
) -> ProjectMember:
    """Add a member to a project. Raises 409 if already a member."""
    existing = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == payload.user_id,
        )
    )
    if existing:
        raise AppHTTPException(
            409, "PROJECT_MEMBER_EXISTS", "User is already a member of this project."
        )
    member = ProjectMember(
        project_id=project_id, user_id=payload.user_id, project_role=payload.project_role
    )
    db.add(member)
    db.flush()
    return member


def update_project_member(
    db: Session, member: ProjectMember, payload: ProjectMemberUpdate
) -> ProjectMember:
    """Update a member's project role."""
    member.project_role = payload.project_role
    return member


def remove_project_member(db: Session, member: ProjectMember) -> None:
    """Hard-delete a project member record."""
    db.delete(member)


def require_project_member_or_404(
    db: Session, project_id: int, member_id: int
) -> ProjectMember:
    """Fetch a project member, or 404 if not found or mismatched project."""
    member = db.get(ProjectMember, member_id)
    if not member or member.project_id != project_id:
        raise not_found("ProjectMember")
    return member
