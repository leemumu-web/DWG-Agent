from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db, require_roles
from backend.app.core.constants import ROLE_AUDITOR, ROLE_SUPER_ADMIN
from backend.app.core.exceptions import not_found
from backend.app.models.audit_log import AuditLog
from backend.app.models.user import User
from backend.app.schemas.audit_schema import AuditLogRead
from backend.app.schemas.common import ok, page

router = APIRouter()


@router.get("")
def list_audit_logs(request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_AUDITOR))):
    logs = list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).all())
    return page([AuditLogRead.model_validate(log) for log in logs], 1, len(logs), len(logs), request.state.request_id)


@router.get("/{audit_log_id}")
def get_audit_log(audit_log_id: int, request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_AUDITOR))):
    log = db.get(AuditLog, audit_log_id)
    if not log:
        raise not_found("AuditLog")
    return ok(AuditLogRead.model_validate(log), request.state.request_id)
