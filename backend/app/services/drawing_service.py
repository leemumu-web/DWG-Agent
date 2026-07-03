from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.drawing import Drawing, DrawingVersion
from app.schemas.drawing_schema import DrawingCreate, DrawingUpdate, DrawingVersionCreate


def create_drawing(
    db: Session, payload: DrawingCreate, created_by: int
) -> Drawing:
    """Create a drawing and optionally its first version."""
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
            created_by=created_by,
        )
        db.add(version)
        db.flush()
        drawing.current_version_id = version.id
    return drawing


def update_drawing(db: Session, drawing: Drawing, payload: DrawingUpdate) -> Drawing:
    """Apply partial update to a drawing's metadata fields."""
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(drawing, key, value)
    return drawing


def archive_drawing(db: Session, drawing: Drawing) -> Drawing:
    """Soft-delete (archive) a drawing."""
    drawing.status = "deleted"
    return drawing


def create_drawing_version(
    db: Session, drawing: Drawing, payload: DrawingVersionCreate, created_by: int
) -> DrawingVersion:
    """Create a new version for a drawing with auto-incremented version_no."""
    max_version = (
        db.scalar(
            select(func.max(DrawingVersion.version_no)).where(
                DrawingVersion.drawing_id == drawing.id
            )
        )
        or 0
    )
    version = DrawingVersion(
        drawing_id=drawing.id,
        file_id=payload.file_id,
        version_no=max_version + 1,
        source=payload.source,
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    drawing.current_version_id = version.id
    return version
