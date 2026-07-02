from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import CurrentUser, get_db
from backend.app.core.config import settings
from backend.app.core.exceptions import not_found, service_unavailable
from backend.app.models.agent_run import AgentRun, AgentRunStep
from backend.app.schemas.agent_schema import AgentRunCreate, AgentRunRead, AgentRunStepRead
from backend.app.schemas.common import ok, page

router = APIRouter()


@router.post("/agent-runs", status_code=status.HTTP_202_ACCEPTED)
def create_agent_run(payload: AgentRunCreate, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    if not settings.agent_enabled:
        raise service_unavailable("AGENT_DISABLED", "Agent subsystem is intentionally disabled in stage 1.")
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
    db.commit()
    return ok(AgentRunRead.model_validate(run), request.state.request_id)


@router.get("/agent-runs/{agent_run_id}")
def get_agent_run(agent_run_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    run = db.get(AgentRun, agent_run_id)
    if not run:
        raise not_found("AgentRun")
    return ok(AgentRunRead.model_validate(run), request.state.request_id)


@router.get("/agent-runs/{agent_run_id}/steps")
def get_agent_run_steps(agent_run_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    steps = list(db.scalars(select(AgentRunStep).where(AgentRunStep.agent_run_id == agent_run_id).order_by(AgentRunStep.id)).all())
    return page([AgentRunStepRead.model_validate(s) for s in steps], 1, len(steps), len(steps), request.state.request_id)


@router.get("/agent-tools")
def list_agent_tools(request: Request):
    if not settings.agent_enabled:
        raise service_unavailable("AGENT_DISABLED", "Agent tools are intentionally disabled in stage 1.")
    return ok([], request.state.request_id)
