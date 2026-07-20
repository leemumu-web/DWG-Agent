"""Truthful readiness diagnostics for Excel Final dependencies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.excel_processing.stage_adapter import (
    ExcelFinalIntegrationError,
    excel_final_dependencies_available,
    get_excel_final_stage_root,
    handbook_database_available,
)
from app.modules.files.interface import get_storage_backend
from app.modules.identity.interface import CurrentUser
from app.platform.config.settings import settings
from app.platform.database.session import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageError

router = APIRouter()


@router.get("/health")
def health_check(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Report feature, Stage, handbook, database and object-storage readiness."""
    is_enabled = settings.excel_final_pipeline_enabled
    try:
        stage_root = get_excel_final_stage_root()
        stage_available = True
    except ExcelFinalIntegrationError:
        stage_root = None
        stage_available = False
    dependencies_available = excel_final_dependencies_available()
    package_available = stage_available and dependencies_available
    handbook_available = bool(stage_root and (stage_root / "handbook.py").is_file())
    handbook_db_available = handbook_database_available() if handbook_available else False

    database_backend = db.get_bind().dialect.name
    try:
        db.execute(text("SELECT 1"))
        database_available = True
    except SQLAlchemyError:
        db.rollback()
        database_available = False
    try:
        get_storage_backend().check_health()
        storage_available = True
    except (AppHTTPException, StorageError):
        storage_available = False

    degraded_components: list[str] = []
    if not is_enabled:
        degraded_components.append("pipeline_disabled")
    if not stage_available:
        degraded_components.append("stage")
    if not dependencies_available:
        degraded_components.append("dependencies")
    if not handbook_available:
        degraded_components.append("handbook_module")
    if not handbook_db_available:
        degraded_components.append("handbook_database")
    if not database_available:
        degraded_components.append("database")
    if not storage_available:
        degraded_components.append("object_storage")
    ready = all(
        (
            is_enabled,
            package_available,
            handbook_available,
            handbook_db_available,
            database_available,
            storage_available,
        )
    )
    return ok(
        {
            "pipeline_enabled": is_enabled,
            "stage_available": stage_available,
            "dependencies_available": dependencies_available,
            "package_available": package_available,
            "handbook_available": handbook_available,
            "handbook_database_available": handbook_db_available,
            "database_backend": database_backend,
            "database_available": database_available,
            "storage_backend": settings.storage_backend,
            "storage_available": storage_available,
            "storage_bucket": settings.minio_bucket_reports,
            "degraded_components": degraded_components,
            "ready": ready,
        },
        request.state.request_id,
    )


__all__ = ["router"]
