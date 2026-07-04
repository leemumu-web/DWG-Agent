from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    get_db,
    has_global_project_access,
    require_project_member,
    require_project_role,
)
from app.core.exceptions import AppHTTPException, not_found
from app.core.validators import validate_sort_by
from app.models.project import Project, ProjectMember
from app.schemas.common import ok, page_from_list
from app.schemas.project_schema import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.audit_service import write_audit_log

router = APIRouter()
PROJECT_WRITE_ROLES = {"project_owner", "project_engineer"}
PROJECT_OWNER_ROLES = {"project_owner"}


@router.get("")
def list_projects(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    # Validate sort_by against whitelist (BUG-13)
    sort_column = validate_sort_by("projects", sort_by)
    sort_dir_value = sort_dir.strip().lower()

    # Validate status filter (BUG-14)
    if status is not None:
        status = status.strip().lower()
        if status not in ("active", "deleted"):
            raise AppHTTPException(
                422,
                "INVALID_STATUS_FILTER",
                "Status must be 'active', 'deleted', or omitted.",
            )

    order_clause = getattr(Project, sort_column)
    if sort_dir_value == "asc":
        order_clause = order_clause.asc()
    else:
        order_clause = order_clause.desc()

    stmt = select(Project).order_by(order_clause)
    if status is not None:
        stmt = stmt.where(Project.status == status)
    else:
        stmt = stmt.where(Project.status != "deleted")
    if not has_global_project_access(current_user):
        stmt = stmt.join(ProjectMember).where(ProjectMember.user_id == current_user.id)
    projects = list(db.scalars(stmt).all())
    return page_from_list(
        [ProjectRead.model_validate(p) for p in projects], page, page_size, request.state.request_id
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if db.scalar(select(Project).where(Project.code == payload.code)):
        raise AppHTTPException(409, "PROJECT_CODE_EXISTS", "Project code already exists.")
    project = Project(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        owner_id=current_user.id,
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
        ProjectMember(project_id=project.id, user_id=current_user.id, project_role="project_owner")
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="projects.create",
        resource_type="project",
        resource_id=project.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(ProjectRead.model_validate(project), request.state.request_id)


@router.get("/{project_id}")
def get_project(
    project_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")
    require_project_member(db, current_user, project.id)
    return ok(ProjectRead.model_validate(project), request.state.request_id)


@router.patch("/{project_id}")
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")
    require_project_role(db, current_user, project.id, PROJECT_WRITE_ROLES)
    before = {"name": project.name, "description": project.description, "status": project.status}
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="projects.update",
        resource_type="project",
        resource_id=project.id,
        before_json=before,
        after_json=payload.model_dump(exclude_unset=True),
        request=request,
    )
    db.commit()
    return ok(ProjectRead.model_validate(project), request.state.request_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")
    require_project_role(db, current_user, project.id, PROJECT_OWNER_ROLES)
    project.status = "deleted"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="projects.delete",
        resource_type="project",
        resource_id=project.id,
        request=request,
    )
    db.commit()
    return None


@router.get("/{project_id}/members")
def list_project_members(
    project_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")
    require_project_member(db, current_user, project.id)
    members = list(
        db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id)).all()
    )
    return page_from_list(
        [ProjectMemberRead.model_validate(m) for m in members],
        page,
        page_size,
        request.state.request_id,
    )


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
def add_project_member(
    project_id: int,
    payload: ProjectMemberCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")
    require_project_role(db, current_user, project.id, PROJECT_OWNER_ROLES)
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
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="project_members.create",
        resource_type="project",
        resource_id=project_id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(ProjectMemberRead.model_validate(member), request.state.request_id)


@router.patch("/{project_id}/members/{member_id}")
def update_project_member(
    project_id: int,
    member_id: int,
    payload: ProjectMemberUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    member = db.get(ProjectMember, member_id)
    if not member or member.project_id != project_id:
        raise not_found("ProjectMember")
    require_project_role(db, current_user, project_id, PROJECT_OWNER_ROLES)
    member.project_role = payload.project_role
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="project_members.update",
        resource_type="project_member",
        resource_id=member.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(ProjectMemberRead.model_validate(member), request.state.request_id)


@router.delete("/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_member(
    project_id: int, member_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    member = db.get(ProjectMember, member_id)
    if not member or member.project_id != project_id:
        raise not_found("ProjectMember")
    require_project_role(db, current_user, project_id, PROJECT_OWNER_ROLES)
    db.delete(member)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="project_members.delete",
        resource_type="project_member",
        resource_id=member.id,
        request=request,
    )
    db.commit()
    return None
