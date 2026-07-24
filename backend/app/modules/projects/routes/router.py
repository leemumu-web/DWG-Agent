"""Project-catalog HTTP route composition."""

from fastapi import APIRouter

from app.modules.projects.routes import drawings, projects

drawings_router = drawings.router
projects_router = projects.router

router = APIRouter()
router.include_router(drawings_router, prefix="/drawings", tags=["drawings"])

__all__ = ["drawings_router", "projects_router", "router"]
