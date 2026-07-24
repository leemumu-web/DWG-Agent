"""Application HTTP composition in stable registration order."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.automation.agent.routes import router as agent_router
from app.modules.excel_processing.routes.router import router as excel_processing_router
from app.modules.files.routes.router import router as files_router
from app.modules.identity.routes.router import roles_router, sessions_router, users_router
from app.modules.jobs.routes.router import jobs_router, results_router, reviews_router
from app.modules.operations.audit.routes import router as audit_router
from app.modules.operations.control_plane.routes import router as control_plane_router
from app.modules.operations.data_catalog.system_routes import router as system_router
from app.modules.operations.router import router as operations_router
from app.modules.projects.routes.router import drawings_router, projects_router
from app.modules.remnant_inventory.routes import (
    import_items_router,
    imports_router,
    materials_router,
    remnants_router,
)
from app.modules.workflows.routes.router import router as workflows_router

api_router = APIRouter()
api_router.include_router(sessions_router, prefix="/auth", tags=["auth"])
api_router.include_router(
    operations_router,
    prefix="/data-admin",
    tags=["data-admin"],
)
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(roles_router, tags=["roles"])
api_router.include_router(files_router, prefix="/files", tags=["files"])
api_router.include_router(
    projects_router,
    prefix="/workflows/projects",
    tags=["workflows"],
)
api_router.include_router(drawings_router, prefix="/workflows/drawings", tags=["workflows"])
api_router.include_router(jobs_router, prefix="/workflows/jobs", tags=["workflows"])
api_router.include_router(results_router, prefix="/workflows/results", tags=["workflows"])
api_router.include_router(reviews_router, prefix="/workflows/reviews", tags=["workflows"])
api_router.include_router(audit_router, prefix="/audit-logs", tags=["audit-logs"])
api_router.include_router(agent_router, tags=["agent-runs"])
api_router.include_router(system_router, prefix="/system", tags=["system"])
api_router.include_router(
    control_plane_router,
    prefix="/control-plane",
    tags=["control-plane"],
)
api_router.include_router(
    excel_processing_router,
    prefix="/excel-final",
    tags=["excel-final"],
)
api_router.include_router(workflows_router, prefix="/workflows")
api_router.include_router(
    materials_router, prefix="/remnant-materials", tags=["remnant-materials"]
)
api_router.include_router(
    imports_router, prefix="/remnant-import-batches", tags=["remnant-imports"]
)
api_router.include_router(
    import_items_router, prefix="/remnant-import-items", tags=["remnant-imports"]
)
api_router.include_router(remnants_router, prefix="/remnants", tags=["remnants"])
