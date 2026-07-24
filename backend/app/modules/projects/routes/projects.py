"""Project and membership HTTP routes mounted below the workflow namespace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.identity.interface import CurrentUser, User, require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.access import (
    has_global_project_access,
    require_project_member,
    require_project_role,
)
from app.modules.projects.models.project import Project, ProjectMember
from app.modules.projects.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.modules.projects.services.projects import (
    add_project_member as add_project_member_record,
)
from app.modules.projects.services.projects import (
    create_project as create_project_record,
)
from app.modules.projects.services.projects import (
    remove_project_member as remove_project_member_record,
)
from app.modules.projects.services.projects import (
    require_project_member_or_404,
)
from app.modules.projects.services.projects import (
    update_project_member as update_project_member_record,
)
from app.platform.config.constants import ROLE_OPERATOR
from app.platform.config.validators import validate_sort_by
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found

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
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    sort_column = validate_sort_by("projects", sort_by)
    sort_dir_value = sort_dir.strip().lower()
    if status_filter is not None:
        status_filter = status_filter.strip().lower()
        if status_filter not in {"active", "deleted"}:
            raise AppHTTPException(
                422,
                "INVALID_STATUS_FILTER",
                "Status must be 'active', 'deleted', or omitted.",
            )

    order_column = getattr(Project, sort_column)
    order_clause = (
        order_column.asc() if sort_dir_value == "asc" else order_column.desc()
    )
    tie_breaker = Project.id.asc() if sort_dir_value == "asc" else Project.id.desc()
    statement = (
        select(Project)
        .options(joinedload(Project.owner))
        .order_by(order_clause, tie_breaker)
    )
    if status_filter is None:
        statement = statement.where(Project.status != "deleted")
    else:
        statement = statement.where(Project.status == status_filter)
    if not has_global_project_access(current_user):
        statement = statement.join(ProjectMember).where(
            ProjectMember.user_id == current_user.id
        )

    projects, total = paginate_scalars(
        db,
        statement,
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [ProjectRead.model_validate(project) for project in projects],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    request: Request,
    current_user: User = Depends(require_roles(ROLE_OPERATOR)),
    db: Session = Depends(get_db),
):
    project = create_project_record(db, payload, owner_id=current_user.id)
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
    db.refresh(project)
    return ok(ProjectRead.model_validate(project), request.state.request_id)


@router.get("/{project_id}")
def get_project(
    project_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
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
    before = {
        "name": project.name,
        "description": project.description,
        "status": project.status,
    }
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(project, key, value)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="projects.update",
        resource_type="project",
        resource_id=project.id,
        before_json=before,
        after_json=changes,
        request=request,
    )
    db.commit()
    return ok(ProjectRead.model_validate(project), request.state.request_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
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
    members, total = paginate_scalars(
        db,
        select(ProjectMember)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [ProjectMemberRead.model_validate(member) for member in members],
        page,
        page_size,
        total,
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
    member = add_project_member_record(db, project_id, payload)
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
    member = require_project_member_or_404(db, project_id, member_id)
    require_project_role(db, current_user, project_id, PROJECT_OWNER_ROLES)
    update_project_member_record(db, member, payload)
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


@router.delete(
    "/{project_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_member(
    project_id: int,
    member_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    member = require_project_member_or_404(db, project_id, member_id)
    require_project_role(db, current_user, project_id, PROJECT_OWNER_ROLES)
    remove_project_member_record(db, member)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="project_members.delete",
        resource_type="project_member",
        resource_id=member.id,
        request=request,
    )
    db.commit()
