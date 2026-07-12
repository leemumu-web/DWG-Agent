from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.core.constants import ROLE_AUDITOR, ROLE_SUPER_ADMIN
from app.core.exceptions import not_found
from app.db.pagination import paginate_scalars
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit_schema import AuditLogRead
from app.schemas.common import ok
from app.schemas.common import page as page_response

router = APIRouter()


@router.get("")
def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    action_domain: str = Query("", max_length=64),
    search: str = Query("", max_length=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_AUDITOR)),
):
    statement = select(AuditLog)
    if action_domain.strip():
        statement = statement.where(
            AuditLog.action.like(f"{action_domain.strip()}.%")
        )
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
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_AUDITOR)),
):
    log = db.get(AuditLog, audit_log_id)
    if not log:
        raise not_found("AuditLog")
    return ok(AuditLogRead.model_validate(log), request.state.request_id)
