"""Stable cross-domain audit write interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

if TYPE_CHECKING:
    from fastapi import Request


def write_audit_log(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request: "Request | None" = None,
) -> AuditLog:
    # Extract IP/UA from the request when callers pass it (§20.4)
    if request is not None and (ip_address is None or user_agent is None):
        if ip_address is None:
            ip_address = request.client.host if request.client else None
        if user_agent is None:
            user_agent = request.headers.get("User-Agent")
    log = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=before_json,
        after_json=after_json,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(log)
    db.flush()
    return log
