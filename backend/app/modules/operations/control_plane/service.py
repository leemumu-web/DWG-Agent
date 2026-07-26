from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.modules.jobs.interface import Job
from app.modules.operations.control_plane.models import (
    ControlPlaneEvent,
    PlatformMessage,
    WorkerRuntime,
)
from app.platform.config.settings import settings

CONTROL_QUEUE_NAMES = (
    "report",
    "dxf_classification",
    "dxf",
    "dxf2dwg",
    "dxf2excel",
    "excel_final",
    "agent",
    "cad",
    "dispatch",
    "maintenance",
)
CONTRACT_ONLY_QUEUES = {"agent", "cad", "dispatch"}
PIPELINE_QUEUE_MAP = {
    "steel_dxf_classifier": "dxf_classification",
    "dxf_open_source": "dxf",
    "dxf2dwg_open_source": "dxf2dwg",
    "dxf2excel": "dxf2excel",
    "excel_final": "excel_final",
    "zwcad_worker": "cad",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _is_before(value: datetime, cutoff: datetime) -> bool:
    """SQLite test doubles return naive datetimes despite timezone-aware columns."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value < cutoff


def _upsert_worker_runtime(
    db: Session,
    *,
    worker_name: str,
    now: datetime,
    status: str,
) -> WorkerRuntime:
    """Create the one runtime row without a concurrent SELECT/INSERT race."""
    values = {
        "worker_name": worker_name,
        "started_at": now,
        "last_seen_at": now,
        "status": status,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert as dialect_insert

        statement = dialect_insert(WorkerRuntime).values(**values)
        statement = statement.on_duplicate_key_update(
            last_seen_at=statement.inserted.last_seen_at,
            updated_at=statement.inserted.updated_at,
        )
        db.execute(statement)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(WorkerRuntime).values(**values)
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[WorkerRuntime.worker_name],
            )
        )
    else:
        runtime = db.scalar(select(WorkerRuntime).where(WorkerRuntime.worker_name == worker_name))
        if runtime is None:
            runtime = WorkerRuntime(**values)
            db.add(runtime)
            db.flush()
        return runtime

    runtime = db.scalar(select(WorkerRuntime).where(WorkerRuntime.worker_name == worker_name))
    if runtime is None:
        raise RuntimeError(f"Worker runtime upsert did not return a row for {worker_name!r}.")
    return runtime


def record_worker_activity(
    db: Session,
    *,
    worker_name: str,
    status: str,
    event_type: str,
    queues: list[str] | None = None,
    concurrency: int | None = None,
    message: str | None = None,
    correlation_id: str | None = None,
) -> WorkerRuntime:
    """Persist a signal observation. Callers must treat failures as non-fatal."""
    now = _now()
    runtime = _upsert_worker_runtime(
        db,
        worker_name=worker_name,
        now=now,
        status=status,
    )
    runtime.status = status
    runtime.hostname = socket.gethostname()
    runtime.process_id = os.getpid()
    runtime.last_seen_at = now
    runtime.stopped_at = now if status == "stopped" else None
    if queues is not None:
        runtime.queues_json = queues
    if concurrency is not None:
        runtime.concurrency = concurrency
    event = ControlPlaneEvent(
        source="worker",
        direction="internal",
        event_type=event_type,
        severity="warning" if status == "stopped" else "info",
        correlation_id=correlation_id,
        target_kind="worker",
        target_id=worker_name,
        payload_json={
            "queues": queues or runtime.queues_json or [],
            "concurrency": concurrency or runtime.concurrency,
        },
        message=message,
    )
    db.add(event)
    if status == "stopped":
        db.add(
            PlatformMessage(
                severity="warning",
                category="worker",
                title=f"Worker 已停止：{worker_name}",
                body=message or "Worker shutdown signal received.",
                related_event_id=None,
            )
        )
    return runtime


def _queue_for_job(job: Job) -> str:
    return PIPELINE_QUEUE_MAP.get(
        job.pipeline or "", "report" if job.task_type == "report" else "default"
    )


def _broker_ready_counts(db: Session) -> tuple[dict[str, int | None], str]:
    bind = db.get_bind()
    if not inspect(bind).has_table("kombu_message") or not inspect(bind).has_table("kombu_queue"):
        return {queue: None for queue in CONTROL_QUEUE_NAMES}, "unavailable"
    try:
        from sqlalchemy import MetaData, Table

        metadata = MetaData()
        messages = Table("kombu_message", metadata, autoload_with=bind)
        queues = Table("kombu_queue", metadata, autoload_with=bind)
        rows = db.execute(
            select(queues.c.name, func.count(messages.c.id))
            .select_from(
                queues.outerjoin(
                    messages, (messages.c.queue_id == queues.c.id) & messages.c.visible.is_(True)
                )
            )
            .group_by(queues.c.name)
        ).all()
        raw = {str(name): int(count) for name, count in rows}
        return {queue: raw.get(queue, 0) for queue in CONTROL_QUEUE_NAMES}, "kombu_message.visible"
    except Exception:
        return {queue: None for queue in CONTROL_QUEUE_NAMES}, "unavailable"


def control_plane_overview(db: Session) -> dict[str, Any]:
    job_rows = db.execute(
        select(Job.status, Job.pipeline, Job.task_type, func.count(Job.id)).group_by(
            Job.status, Job.pipeline, Job.task_type
        )
    ).all()
    business: dict[str, dict[str, int]] = {
        queue: {"queued": 0, "running": 0, "failed": 0} for queue in CONTROL_QUEUE_NAMES
    }
    for status, pipeline, task_type, count in job_rows:
        queue = PIPELINE_QUEUE_MAP.get(
            pipeline or "", "report" if task_type == "report" else "default"
        )
        if queue in business and status in business[queue]:
            business[queue][status] += int(count)
    ready, ready_source = _broker_ready_counts(db)
    stale_before = _now() - timedelta(seconds=settings.control_plane_worker_stale_seconds)
    runtimes = list(db.scalars(select(WorkerRuntime).order_by(WorkerRuntime.last_seen_at.desc())))
    workers = [
        {
            "id": row.id,
            "worker_name": row.worker_name,
            "hostname": row.hostname,
            "process_id": row.process_id,
            "queues": row.queues_json or [],
            "concurrency": row.concurrency,
            "status": "stale"
            if row.status == "online" and _is_before(row.last_seen_at, stale_before)
            else row.status,
            "started_at": row.started_at,
            "last_seen_at": row.last_seen_at,
            "stopped_at": row.stopped_at,
        }
        for row in runtimes
    ]
    return {
        "checked_at": _now().isoformat(),
        "broker": {
            "kind": "mysql_sqlalchemy",
            "url_scheme": "sqla+mysql",
            "ready_count_source": ready_source,
            "limitations": [
                "ready counts exclude reserved or in-flight tasks",
                "Celery remote control and broadcast events are disabled for this transport",
            ],
        },
        "queues": [
            {
                "name": name,
                "business_jobs": business[name],
                "broker_ready_messages": ready[name],
                "mode": "contract_only" if name in CONTRACT_ONLY_QUEUES else "active",
            }
            for name in CONTROL_QUEUE_NAMES
        ],
        "workers": workers,
        "summary": {
            "registered_workers": len(workers),
            "online_workers": sum(item["status"] == "online" for item in workers),
            "stale_workers": sum(item["status"] == "stale" for item in workers),
            "unread_messages": int(
                db.scalar(
                    select(func.count(PlatformMessage.id)).where(PlatformMessage.status == "unread")
                )
                or 0
            ),
        },
        "implementation": {
            "rabbitmq": "pending",
            "celery_beat": "pending",
            "durable_outbox": "pending",
            "windows_node_agent": "pending",
        },
    }
