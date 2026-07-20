"""Application HTTP composition in stable registration order."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agent_runs_api,
    audit_logs_api,
    control_plane_api,
    data_admin_api,
    excel_final_api,
    jobs_api,
    results_api,
    reviews_api,
    system_api,
    workflow_inputs_api,
    workflows_api,
)
from app.modules.files.routes.router import router as files_router
from app.modules.identity.routes.router import roles_router, sessions_router, users_router
from app.modules.projects.routes.router import drawings_router, projects_router

api_router = APIRouter()
api_router.include_router(sessions_router, prefix="/auth", tags=["auth"])
api_router.include_router(
    data_admin_api.router,
    prefix="/data-admin",
    tags=["data-admin"],
)
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(roles_router, tags=["roles"])
api_router.include_router(projects_router, prefix="/projects", tags=["projects"])
api_router.include_router(files_router, prefix="/files", tags=["files"])
api_router.include_router(drawings_router, prefix="/drawings", tags=["drawings"])
api_router.include_router(jobs_api.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(results_api.router, prefix="/results", tags=["results"])
api_router.include_router(reviews_api.router, prefix="/reviews", tags=["reviews"])
api_router.include_router(audit_logs_api.router, prefix="/audit-logs", tags=["audit-logs"])
api_router.include_router(agent_runs_api.router, tags=["agent-runs"])
api_router.include_router(system_api.router, prefix="/system", tags=["system"])
api_router.include_router(control_plane_api.router, prefix="/control-plane", tags=["control-plane"])
api_router.include_router(excel_final_api.router, prefix="/excel-final", tags=["excel-final"])
api_router.include_router(workflows_api.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(
    workflow_inputs_api.router,
    prefix="/workflows",
    tags=["workflow-inputs"],
)
