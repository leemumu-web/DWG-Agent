"""Compose Excel Final routes with every static path before parameters."""

from fastapi import APIRouter

from app.modules.excel_processing.routes import catalog, health, processing, tools

router = APIRouter()

for child in (
    processing.static_router,
    catalog.static_router,
    tools.router,
    health.router,
    processing.item_router,
    catalog.item_router,
):
    router.routes.extend(child.routes)

__all__ = ["router"]
