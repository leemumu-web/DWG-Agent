from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.automation.agent.models.runs import AgentRun, AgentRunStep
from app.modules.automation.agent.schemas import AgentRunCreate, AgentRunRead, AgentRunStepRead
from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import has_global_project_access, require_project_member
from app.platform.config.settings import settings
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import forbidden, not_found, service_unavailable

router = APIRouter()


def _get_accessible_agent_run(
    db: Session, current_user: CurrentUser, agent_run_id: int
) -> AgentRun:
    run = db.get(AgentRun, agent_run_id)
    if not run:
        raise not_found("AgentRun")
    if has_global_project_access(current_user) or run.user_id == current_user.id:
        return run
    if run.project_id is not None:
        require_project_member(db, current_user, run.project_id)
        return run
    raise forbidden("Agent run access is restricted.")


@router.post("/agent-runs", status_code=status.HTTP_202_ACCEPTED)
def create_agent_run(
    payload: AgentRunCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if not settings.agent_enabled:
        raise service_unavailable(
            "AGENT_DISABLED", "Agent subsystem is intentionally disabled in stage 1."
        )
    run = AgentRun(
        session_id=payload.session_id,
        user_id=current_user.id,
        project_id=payload.context.get("project_id"),
        drawing_id=payload.context.get("drawing_id"),
        file_id=payload.file_id,
        task=payload.task,
        status="queued",
    )
    db.add(run)
    db.flush()
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="agent_runs.create",
        resource_type="agent_run",
        resource_id=run.id,
        request=request,
    )
    db.commit()
    return ok(AgentRunRead.model_validate(run), request.state.request_id)


@router.get("/agent-runs/{agent_run_id}")
def get_agent_run(
    agent_run_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    if not settings.agent_enabled:
        raise service_unavailable(
            "AGENT_DISABLED", "Agent subsystem is intentionally disabled in stage 1."
        )
    run = _get_accessible_agent_run(db, current_user, agent_run_id)
    return ok(AgentRunRead.model_validate(run), request.state.request_id)


@router.get("/agent-runs/{agent_run_id}/steps")
def get_agent_run_steps(
    agent_run_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if not settings.agent_enabled:
        raise service_unavailable(
            "AGENT_DISABLED", "Agent subsystem is intentionally disabled in stage 1."
        )
    _get_accessible_agent_run(db, current_user, agent_run_id)
    steps, total = paginate_scalars(
        db,
        select(AgentRunStep)
        .where(AgentRunStep.agent_run_id == agent_run_id)
        .order_by(AgentRunStep.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [AgentRunStepRead.model_validate(s) for s in steps],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/agent-tools")
def list_agent_tools(request: Request, current_user: CurrentUser):
    if not settings.agent_enabled:
        raise service_unavailable(
            "AGENT_DISABLED", "Agent tools are intentionally disabled in stage 1."
        )
    return ok([], request.state.request_id)
