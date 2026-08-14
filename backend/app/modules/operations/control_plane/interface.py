"""Stable control-plane write boundary for workers and other operation modules.

Calling contract:

- ``event_type`` should follow a dotted convention (e.g. ``worker.heartbeat``,
  ``worker.stopped``) so consumers can filter and group events.
- ``severity`` is one of ``info`` / ``warning`` (``error`` reserved for
  future use); worker signals use ``warning`` when a worker stops.
- ``direction`` is ``internal`` (default) or ``external`` (Windows/Node
  contracts); keep it stable for filtering.
- ``correlation_id`` links related events across sources; pass it through
  from the originating job/run when available.
- ``register_control_plane_worker_observer`` must be called during worker
  assembly (bootstrap), before workers start, so signal callbacks are wired.
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
