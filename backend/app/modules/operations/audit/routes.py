from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, require_roles
from app.modules.operations.audit.models import AuditLog
from app.modules.operations.audit.schemas import AuditLogRead
from app.platform.config.constants import ROLE_SUPER_ADMIN
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import not_found

router = APIRouter()


@router.get("")
def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    action_domain: str = Query("", max_length=64),
    search: str = Query("", max_length=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN)),
):
    statement = select(AuditLog)
    if action_domain.strip():
        statement = statement.where(AuditLog.action.like(f"{action_domain.strip()}.%"))
    if search.strip():
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AuditLog.action.ilike(pattern),
                AuditLog.resource_type.ilike(pattern),
                cast(AuditLog.id, String).like(pattern),
                cast(AuditLog.actor_user_id, String).like(pattern),
                cast(AuditLog.resource_id, String).like(pattern),
            )
        )
    logs, total = paginate_scalars(
        db,
        statement.order_by(AuditLog.id.desc()),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [AuditLogRead.model_validate(log) for log in logs],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/{audit_log_id}")
def get_audit_log(
    audit_log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN)),
):
    log = db.get(AuditLog, audit_log_id)
    if not log:
        raise not_found("AuditLog")
    return ok(AuditLogRead.model_validate(log), request.state.request_id)
