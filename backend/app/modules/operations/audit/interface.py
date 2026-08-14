"""Stable cross-domain audit write interface.

Calling contract:

- ``action`` must use the dotted ``<domain>.<action>`` convention (e.g.
  ``files.delete``); the audit list endpoint filters by this prefix, so
  callers must not invent free-form action names.
- ``resource_type`` is a free-form stable noun (e.g. ``project``,
  ``stored_file``); keep the spelling stable across call sites so the
  resource can be traced.
- ``before_json`` / ``after_json`` are pre/post change snapshots of the
  mutated row (or None when not applicable); do not store blobs.
- ``request`` is optional convenience: when passed (and IP/UA not already
  given) the client host and User-Agent are extracted from it.
- The row is flushed inside the caller's transaction — audit writes join
  the business transaction and roll back with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.operations.audit.models import AuditLog

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
    """Write one audit row (``<domain>.<action>`` dotted convention).

    See module docstring for the action/resource naming contract. Flushed
    into the caller's transaction; nothing is committed here.
    """
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


def latest_audit_log(
    db: Session,
    *,
    actions: set[str],
    resource_type: str,
    resource_id: int,
) -> AuditLog | None:
    """Read the latest matching event without exposing audit persistence internals."""
    return db.scalar(
        select(AuditLog)
        .where(
            AuditLog.action.in_(actions),
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == resource_id,
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )


__all__ = ["latest_audit_log", "write_audit_log"]
