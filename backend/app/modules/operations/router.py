"""Compose the stable /data-admin route order from operation owners."""

from fastapi import APIRouter

from app.modules.operations.daily_archive.routes import router as archive_router
from app.modules.operations.data_catalog.routes import router as catalog_router
from app.modules.operations.data_catalog.mysql_routes import router as mysql_router
from app.modules.operations.data_catalog.object_mutations import router as object_mutations_router
from app.modules.operations.storage_reconciliation.routes import (
    router as reconciliation_router,
)

router = APIRouter()


def _mount(child: APIRouter) -> None:
    """Mount zero-prefix routes without FastAPI's lazy included-router wrapper."""
    router.routes.extend(child.routes)


_mount(archive_router)
_mount(mysql_router)
_mount(object_mutations_router)
_mount(catalog_router)
_mount(reconciliation_router)

__all__ = ["router"]
