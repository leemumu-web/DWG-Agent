from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agent_runs_api,
    audit_logs_api,
    auth_api,
    control_plane_api,
    data_admin_api,
    drawings_api,
    excel_final_api,
    files_api,
    jobs_api,
    projects_api,
    results_api,
    reviews_api,
    roles_api,
    system_api,
    users_api,
    workflow_inputs_api,
    workflows_api,
)

api_router = APIRouter()
api_router.include_router(auth_api.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    data_admin_api.router,
    prefix="/data-admin",
    tags=["data-admin"],
)
api_router.include_router(users_api.router, prefix="/users", tags=["users"])
api_router.include_router(roles_api.router, tags=["roles"])
api_router.include_router(projects_api.router, prefix="/projects", tags=["projects"])
api_router.include_router(files_api.router, prefix="/files", tags=["files"])
api_router.include_router(drawings_api.router, prefix="/drawings", tags=["drawings"])
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
