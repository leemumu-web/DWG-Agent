"""Stable control-plane write boundary for workers and other operation modules."""

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
