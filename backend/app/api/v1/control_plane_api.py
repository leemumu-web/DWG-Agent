from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import DbSession, require_roles
from app.core.constants import ROLE_ADMIN, ROLE_AUDITOR
from app.db.pagination import paginate_scalars
from app.models.control_plane import ControlPlaneEvent, PlatformMessage
from app.schemas.common import ok, page
from app.services.control_plane_service import control_plane_overview, windows_node_contract

router = APIRouter()
reader = require_roles(ROLE_ADMIN, ROLE_AUDITOR)


def _event(row: ControlPlaneEvent) -> dict:
    return {"id": row.id, "source": row.source, "direction": row.direction, "event_type": row.event_type,
            "severity": row.severity, "correlation_id": row.correlation_id, "target_kind": row.target_kind,
            "target_id": row.target_id, "payload": row.payload_json, "message": row.message, "created_at": row.created_at}


def _message(row: PlatformMessage) -> dict:
    return {"id": row.id, "audience": row.audience, "severity": row.severity, "category": row.category,
            "title": row.title, "body": row.body, "status": row.status, "action_url": row.action_url,
            "related_event_id": row.related_event_id, "read_at": row.read_at, "created_at": row.created_at}


@router.get("/overview")
def get_overview(request: Request, db: DbSession, _user=Depends(reader)):
    """Actual MySQL-broker queue and worker control-plane snapshot."""
    return ok(control_plane_overview(db), request.state.request_id)


@router.get("/events")
def list_events(request: Request, db: DbSession, page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(30, ge=1, le=100), _user=Depends(reader)):
    rows, total = paginate_scalars(db, select(ControlPlaneEvent).order_by(ControlPlaneEvent.created_at.desc()), page_no=page_no, page_size=page_size)
    return page([_event(row) for row in rows], page_no, page_size, total, request.state.request_id)


@router.get("/messages")
def list_messages(request: Request, db: DbSession, page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(30, ge=1, le=100), _user=Depends(reader)):
    rows, total = paginate_scalars(db, select(PlatformMessage).order_by(PlatformMessage.created_at.desc()), page_no=page_no, page_size=page_size)
    return page([_message(row) for row in rows], page_no, page_size, total, request.state.request_id)


@router.patch("/messages/{message_id}/read")
def mark_message_read(message_id: int, request: Request, db: DbSession, _user=Depends(reader)):
    row = db.get(PlatformMessage, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Platform message not found")
    if row.status != "read":
        row.status, row.read_at = "read", datetime.now(UTC)
        db.commit()
        db.refresh(row)
    return ok(_message(row), request.state.request_id)


@router.get("/contracts/windows-node-agent")
def get_windows_node_contract(request: Request, _user=Depends(reader)):
    """Published draft only; no Windows node endpoints are active yet."""
    return ok(windows_node_contract(), request.state.request_id)
