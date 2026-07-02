from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db
from app.core.exceptions import not_found
from app.models.drawing import Drawing, DrawingVersion
from app.schemas.common import ok, page
from app.schemas.drawing_schema import (
    DrawingCreate,
    DrawingRead,
    DrawingUpdate,
    DrawingVersionCreate,
    DrawingVersionRead,
)
from app.services.audit_service import write_audit_log

router = APIRouter()


@router.get("")
def list_drawings(request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    drawings = list(db.scalars(select(Drawing).where(Drawing.status != "deleted").order_by(Drawing.id.desc())).all())
    return page([DrawingRead.model_validate(d) for d in drawings], 1, len(drawings), len(drawings), request.state.request_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_drawing(payload: DrawingCreate, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    drawing = Drawing(project_id=payload.project_id, drawing_no=payload.drawing_no, title=payload.title, discipline=payload.discipline, status="active")
    db.add(drawing)
    db.flush()
    if payload.file_id:
        version = DrawingVersion(drawing_id=drawing.id, file_id=payload.file_id, version_no=1, source="initial", created_by=current_user.id)
        db.add(version)
        db.flush()
        drawing.current_version_id = version.id
    write_audit_log(db, actor_user_id=current_user.id, action="drawings.create", resource_type="drawing", resource_id=drawing.id, after_json=payload.model_dump())
    db.commit()
    return ok(DrawingRead.model_validate(drawing), request.state.request_id)


@router.get("/{drawing_id}")
def get_drawing(drawing_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    drawing = db.get(Drawing, drawing_id)
    if not drawing or drawing.status == "deleted":
        raise not_found("Drawing")
    return ok(DrawingRead.model_validate(drawing), request.state.request_id)


@router.patch("/{drawing_id}")
def update_drawing(drawing_id: int, payload: DrawingUpdate, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    drawing = db.get(Drawing, drawing_id)
    if not drawing:
        raise not_found("Drawing")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(drawing, key, value)
    write_audit_log(db, actor_user_id=current_user.id, action="drawings.update", resource_type="drawing", resource_id=drawing.id, after_json=payload.model_dump(exclude_unset=True))
    db.commit()
    return ok(DrawingRead.model_validate(drawing), request.state.request_id)


@router.delete("/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_drawing(drawing_id: int, db: Session = Depends(get_db), current_user: CurrentUser = None):
    drawing = db.get(Drawing, drawing_id)
    if not drawing:
        raise not_found("Drawing")
    drawing.status = "deleted"
    write_audit_log(db, actor_user_id=current_user.id, action="drawings.delete", resource_type="drawing", resource_id=drawing.id)
    db.commit()
    return None


@router.get("/{drawing_id}/versions")
def list_versions(drawing_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    versions = list(db.scalars(select(DrawingVersion).where(DrawingVersion.drawing_id == drawing_id).order_by(DrawingVersion.version_no)).all())
    return page([DrawingVersionRead.model_validate(v) for v in versions], 1, len(versions), len(versions), request.state.request_id)


@router.post("/{drawing_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(drawing_id: int, payload: DrawingVersionCreate, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    drawing = db.get(Drawing, drawing_id)
    if not drawing:
        raise not_found("Drawing")
    max_version = db.scalar(select(func.max(DrawingVersion.version_no)).where(DrawingVersion.drawing_id == drawing_id)) or 0
    version = DrawingVersion(drawing_id=drawing_id, file_id=payload.file_id, version_no=max_version + 1, source=payload.source, created_by=current_user.id)
    db.add(version)
    db.flush()
    drawing.current_version_id = version.id
    write_audit_log(db, actor_user_id=current_user.id, action="drawing_versions.create", resource_type="drawing", resource_id=drawing.id, after_json=payload.model_dump())
    db.commit()
    return ok(DrawingVersionRead.model_validate(version), request.state.request_id)


@router.get("/{drawing_id}/preview")
def get_preview(drawing_id: int, request: Request):
    return ok({"drawing_id": drawing_id, "preview": None, "message": "Preview generation is not implemented in stage 1."}, request.state.request_id)
