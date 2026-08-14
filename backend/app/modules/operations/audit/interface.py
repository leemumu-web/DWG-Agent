"""稳定的跨域审计写入接口。

调用契约：

- ``action`` 必须使用 ``<domain>.<action>`` 点分命名约定（如
  ``files.delete``）；审计列表接口按此前缀过滤，调用方不得发明自由格式的
  动作名。
- ``resource_type`` 是自由但稳定的名词（如 ``project``、
  ``stored_file``）；各调用点的拼写必须保持一致，以便追踪资源。
- ``before_json`` / ``after_json`` 是被改行的变更前后快照（不适用时为
  None）；不要存大对象。
- ``request`` 是可选便捷参数：传入时（且未显式给 IP/UA）从请求提取客户端
  host 与 User-Agent。
- 行在调用方事务内 flush——审计写入与业务事务同生共死，随事务一起回滚。
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
    """写入一条审计行（``<domain>.<action>`` 点分约定）。

    动作/资源命名契约见模块 docstring。在调用方事务内 flush；此处不提交。
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
