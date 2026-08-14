"""worker 与其他运维模块的稳定控制面写入边界。

调用契约：

- ``event_type`` 应遵循点分约定（如 ``worker.heartbeat``、
  ``worker.stopped``），便于消费方过滤与分组。
- ``severity`` 取值 ``info`` / ``warning``（``error`` 预留）；worker 停止时
  信号用 ``warning``。
- ``direction`` 取 ``internal``（默认）或 ``external``（Windows/Node
  契约）；保持稳定以便过滤。
- ``correlation_id`` 跨来源关联相关事件；有来源 job/run 时透传。
- ``register_control_plane_worker_observer`` 必须在 worker 装配
  （bootstrap）阶段、worker 启动前调用，以便接线信号回调。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.operations.control_plane.models import ControlPlaneEvent
from app.modules.operations.control_plane.service import record_worker_activity
from app.platform.database.session import SessionLocal


def record_control_plane_event(
    db: Session,
    *,
    source: str,
    event_type: str,
    severity: str = "info",
    direction: str = "internal",
    target_kind: str | None = None,
    target_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
    message: str | None = None,
) -> ControlPlaneEvent:
    event = ControlPlaneEvent(
        source=source,
        direction=direction,
        event_type=event_type,
        severity=severity,
        target_kind=target_kind,
        target_id=target_id,
        correlation_id=correlation_id,
        payload_json=payload,
        message=message,
    )
    db.add(event)
    return event


def _persist_worker_signal(
    *,
    worker_name: str,
    status: str,
    event_type: str,
    queues: list[str],
    concurrency: int,
    correlation_id: str | None,
) -> None:
    with SessionLocal() as db:
        record_worker_activity(
            db,
            worker_name=worker_name,
            status=status,
            event_type=event_type,
            queues=queues,
            concurrency=concurrency,
            correlation_id=correlation_id,
        )
        db.commit()


def register_control_plane_worker_observer() -> None:
    from app.platform.messaging.celery_app import register_worker_signal_callback

    register_worker_signal_callback(
        "operations.control_plane",
        _persist_worker_signal,
    )


__all__ = [
    "record_control_plane_event",
    "record_worker_activity",
    "register_control_plane_worker_observer",
]
