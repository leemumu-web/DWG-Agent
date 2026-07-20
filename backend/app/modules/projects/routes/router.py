"""Project-catalog HTTP route composition."""

from fastapi import APIRouter

from app.modules.projects.routes import drawings, projects

projects_router = projects.router
drawings_router = drawings.router

router = APIRouter()
router.include_router(projects_router, prefix="/projects", tags=["projects"])
router.include_router(drawings_router, prefix="/drawings", tags=["drawings"])

__all__ = ["drawings_router", "projects_router", "router"]
