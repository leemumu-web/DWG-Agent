"""Drawing and version catalog HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.access import (
    has_global_project_access,
    require_active_project,
    require_project_member,
    require_project_role,
)
from app.modules.projects.models.drawing import Drawing, DrawingVersion
from app.modules.projects.models.project import Project, ProjectMember
from app.modules.projects.schemas.drawing import (
    DrawingCreate,
    DrawingRead,
    DrawingUpdate,
    DrawingVersionCreate,
    DrawingVersionRead,
)
from app.platform.config.validators import validate_sort_by
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import not_found

router = APIRouter()
PROJECT_WRITE_ROLES = {"project_owner", "project_engineer"}


@router.get("")
def list_drawings(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    sort_column = validate_sort_by("drawings", sort_by)
    sort_dir_value = sort_dir.strip().lower()
    order_clause = getattr(Drawing, sort_column)
    if sort_dir_value == "asc":
        order_clause = order_clause.asc()
    else:
        order_clause = order_clause.desc()
    tie_breaker = Drawing.id.asc() if sort_dir_value == "asc" else Drawing.id.desc()
    stmt = (
        select(Drawing)
        .where(Drawing.status != "deleted")
        .order_by(order_clause, tie_breaker)
    )
    if not has_global_project_access(current_user):
        stmt = stmt.join(ProjectMember, ProjectMember.project_id == Drawing.project_id).where(
            ProjectMember.user_id == current_user.id
        )
    drawings, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    return page_response(
        [DrawingRead.model_validate(d) for d in drawings],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_drawing(
    payload: DrawingCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    project = db.get(Project, payload.project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")
    require_project_role(db, current_user, payload.project_id, PROJECT_WRITE_ROLES)
    drawing = Drawing(
        project_id=payload.project_id,
        drawing_no=payload.drawing_no,
        title=payload.title,
        discipline=payload.discipline,
        status="active",
    )
    db.add(drawing)
    db.flush()
    if payload.file_id:
        version = DrawingVersion(
            drawing_id=drawing.id,
            file_id=payload.file_id,
            version_no=1,
            source="initial",
            created_by=current_user.id,
        )
        db.add(version)
        db.flush()
        drawing.current_version_id = version.id
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="drawings.create",
        resource_type="drawing",
        resource_id=drawing.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(DrawingRead.model_validate(drawing), request.state.request_id)


@router.get("/{drawing_id}")
def get_drawing(
    drawing_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    require_active_project(db, drawing.project_id)
    require_project_member(db, current_user, drawing.project_id)
    return ok(DrawingRead.model_validate(drawing), request.state.request_id)


@router.patch("/{drawing_id}")
def update_drawing(
    drawing_id: int,
    payload: DrawingUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    require_active_project(db, drawing.project_id)
    require_project_role(db, current_user, drawing.project_id, PROJECT_WRITE_ROLES)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(drawing, key, value)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="drawings.update",
        resource_type="drawing",
        resource_id=drawing.id,
        after_json=payload.model_dump(exclude_unset=True),
        request=request,
    )
    db.commit()
    return ok(DrawingRead.model_validate(drawing), request.state.request_id)


@router.delete("/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_drawing(drawing_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)):
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    require_active_project(db, drawing.project_id)
    require_project_role(db, current_user, drawing.project_id, PROJECT_WRITE_ROLES)
    drawing.status = "deleted"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="drawings.delete",
        resource_type="drawing",
        resource_id=drawing.id,
        request=request,
    )
    db.commit()
    return None


@router.get("/{drawing_id}/versions")
def list_versions(
    drawing_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    require_active_project(db, drawing.project_id)
    require_project_member(db, current_user, drawing.project_id)
    versions, total = paginate_scalars(
        db,
        select(DrawingVersion)
        .where(DrawingVersion.drawing_id == drawing_id)
        .order_by(DrawingVersion.version_no, DrawingVersion.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [DrawingVersionRead.model_validate(v) for v in versions],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("/{drawing_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    drawing_id: int,
    payload: DrawingVersionCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    require_project_role(db, current_user, drawing.project_id, PROJECT_WRITE_ROLES)
    max_version = (
        db.scalar(
            select(func.max(DrawingVersion.version_no)).where(
                DrawingVersion.drawing_id == drawing_id
            )
        )
        or 0
    )
    version = DrawingVersion(
        drawing_id=drawing_id,
        file_id=payload.file_id,
        version_no=max_version + 1,
        source=payload.source,
        created_by=current_user.id,
    )
    db.add(version)
    db.flush()
    drawing.current_version_id = version.id
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="drawing_versions.create",
        resource_type="drawing",
        resource_id=drawing.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(DrawingVersionRead.model_validate(version), request.state.request_id)


@router.get("/{drawing_id}/preview")
def get_preview(
    drawing_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    # Stage 1 keeps preview generation disabled, but the endpoint still enforces auth.
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    require_project_member(db, current_user, drawing.project_id)
    return ok(
        {
            "drawing_id": drawing_id,
            "preview": None,
            "message": "Preview generation is not implemented in stage 1.",
        },
        request.state.request_id,
    )
