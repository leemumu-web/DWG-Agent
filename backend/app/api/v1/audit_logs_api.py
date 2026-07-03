from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.core.constants import ROLE_AUDITOR, ROLE_SUPER_ADMIN
from app.core.exceptions import not_found
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit_schema import AuditLogRead
from app.schemas.common import ok, page_from_list

router = APIRouter()


@router.get("")
def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_AUDITOR)),
):
    logs = list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).all())
    return page_from_list(
        [AuditLogRead.model_validate(log) for log in logs],
        page,
        page_size,
        request.state.request_id,
    )


@router.get("/{audit_log_id}")
def get_audit_log(
    audit_log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_AUDITOR)),
):
    log = db.get(AuditLog, audit_log_id)
    if not log:
        raise not_found("AuditLog")
    return ok(AuditLogRead.model_validate(log), request.state.request_id)
