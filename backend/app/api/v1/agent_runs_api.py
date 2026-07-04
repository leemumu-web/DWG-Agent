from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db
from app.core.config import settings
from app.core.exceptions import not_found, service_unavailable
from app.models.agent_run import AgentRun, AgentRunStep
from app.schemas.agent_schema import AgentRunCreate, AgentRunRead, AgentRunStepRead
from app.schemas.common import ok, page_from_list
from app.services.audit_service import write_audit_log

router = APIRouter()


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
    run = db.get(AgentRun, agent_run_id)
    if not run:
        raise not_found("AgentRun")
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
    steps = list(
        db.scalars(
            select(AgentRunStep)
            .where(AgentRunStep.agent_run_id == agent_run_id)
            .order_by(AgentRunStep.id)
        ).all()
    )
    return page_from_list(
        [AgentRunStepRead.model_validate(s) for s in steps],
        page,
        page_size,
        request.state.request_id,
    )


@router.get("/agent-tools")
def list_agent_tools(request: Request, current_user: CurrentUser):
    if not settings.agent_enabled:
        raise service_unavailable(
            "AGENT_DISABLED", "Agent tools are intentionally disabled in stage 1."
        )
    return ok([], request.state.request_id)
