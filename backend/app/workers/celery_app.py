from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import Celery
from celery.signals import worker_process_init, worker_ready
from sqlalchemy import MetaData, Table, delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.constants import JOB_FAILED, JOB_RUNNING
from app.db.session import SessionLocal, engine
from app.models.job import Job

logger = logging.getLogger(__name__)

_celery_engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": settings.db_pool_recycle_seconds,
    "pool_size": 1,
    "max_overflow": 1,
    "pool_timeout": settings.db_pool_timeout_seconds,
    "pool_use_lifo": True,
}

celery_app = Celery(
    "dwg_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks_agent",
        "app.workers.tasks_cad",
        "app.workers.tasks_dxf",
        "app.workers.tasks_dxf2dwg",
        "app.workers.tasks_dxf2excel",
        "app.workers.tasks_excel_final",
        "app.workers.tasks_report",
    ],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    # ── MySQL-backed SQLAlchemy transport ─────────────────────────────────
    broker_transport_options={
        "max_retries": 3,
        "interval_start": 0.5,
        "interval_step": 0.5,
        "interval_max": 3,
        **_celery_engine_options,
    },
    # These settings let Celery requeue after a child-process loss while the
    # worker parent remains alive. The SQL transport cannot restore delivery
    # after the entire worker/host dies; stale running jobs are reconciled below.
    task_reject_on_worker_lost=True,
    task_acks_late=True,
    # Process one task at a time in the dxf2excel queue so progress events
    # map to a single job; other queues can be tuned separately.
    worker_prefetch_multiplier=1,
    # ── existing settings ─────────────────────────────────────────────────
    enable_utc=True,
    result_expires=24 * 60 * 60,
    database_engine_options=_celery_engine_options,
    database_short_lived_sessions=True,
    result_serializer="json",
    task_always_eager=settings.celery_task_always_eager,
    task_default_queue="default",
    task_eager_propagates=True,
    task_routes={
        "app.workers.tasks_agent.*": {"queue": "agent"},
        "app.workers.tasks_dxf.*": {"queue": "dxf"},
        "app.workers.tasks_dxf2dwg.*": {"queue": "dxf2dwg"},
        "app.workers.tasks_dxf2excel.*": {"queue": "dxf2excel"},
        "app.workers.tasks_excel_final.*": {"queue": "excel_final"},
        "app.workers.tasks_cad.*": {"queue": "cad"},
        "app.workers.tasks_report.*": {"queue": "report"},
    },
    task_serializer="json",
    task_track_started=True,
    timezone="UTC",
    # Kombu's SQLAlchemy transport has no fanout exchange, so remote-control
    # and event-monitoring broadcasts are intentionally disabled.
    task_send_sent_event=False,
    worker_send_task_events=False,
)


def cleanup_expired_task_results(app: Celery = celery_app) -> None:
    """Bound growth of Celery's MySQL result tables on every worker start."""
    try:
        app.backend.cleanup()
    except Exception:
        logger.exception("Failed to clean expired Celery result rows")


def cleanup_consumed_broker_messages(
    db_engine: Engine = engine,
    *,
    table_name: str = "kombu_message",
) -> int:
    """Delete SQL-transport rows already delivered to a worker.

    Kombu marks rows invisible when consuming them but does not delete them on
    acknowledgement. Invisible rows are no longer consumable; removing them is
    safe even if the corresponding child task is still finishing.
    """
    metadata = MetaData()
    message_table = Table(table_name, metadata, autoload_with=db_engine)
    if "visible" not in message_table.c:
        raise RuntimeError(f"{table_name} has no visible column")
    with db_engine.begin() as connection:
        result = connection.execute(delete(message_table).where(message_table.c.visible.is_(False)))
    return result.rowcount or 0


def reconcile_stale_running_jobs(
    session_factory: sessionmaker = SessionLocal,
    *,
    timeout_seconds: int | None = None,
) -> int:
    """Fail running jobs abandoned by a dead SQL-broker worker.

    A second conditional UPDATE protects jobs that complete or emit progress
    after the candidate scan but before reconciliation.
    """
    timeout = timeout_seconds or settings.celery_stale_job_timeout_seconds
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=timeout)
    recovered = 0
    with session_factory() as db:
        candidates = db.execute(
            select(Job.id, Job.progress).where(
                Job.status == JOB_RUNNING,
                Job.updated_at < cutoff,
            )
        ).all()
        for job_id, progress in candidates:
            message = (
                f"Worker stopped updating this job for more than {timeout} seconds. "
                "Retry the job after verifying the queue worker is healthy."
            )
            event = {
                "type": "error",
                "status": JOB_FAILED,
                "progress": progress or 0,
                "error_code": "CELERY_WORKER_LOST",
                "error_message": message,
                "message": message,
                "job_id": job_id,
            }
            result = db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JOB_RUNNING,
                    Job.updated_at < cutoff,
                )
                .values(
                    status=JOB_FAILED,
                    error_code="CELERY_WORKER_LOST",
                    error_message=message,
                    progress_data=event,
                    finished_at=now,
                    updated_at=now,
                )
            )
            recovered += result.rowcount or 0
        db.commit()
    return recovered


def dispose_inherited_resources(db_engine: Engine = engine) -> None:
    """Drop application DB connections inherited across Celery's fork boundary."""
    from app.services.storage_service import clear_storage_backend_cache

    db_engine.dispose(close=False)
    clear_storage_backend_cache()


@worker_process_init.connect
def _dispose_resources_in_worker_child(**_kwargs) -> None:
    dispose_inherited_resources()


@worker_ready.connect
def _maintain_mysql_runtime_on_worker_start(sender=None, **_kwargs) -> None:
    cleanup_expired_task_results(getattr(sender, "app", celery_app))
    try:
        removed = cleanup_consumed_broker_messages()
        if removed:
            logger.info("Removed %s consumed SQL broker rows", removed)
    except Exception:
        logger.exception("Failed to clean consumed SQL broker rows")
    try:
        recovered = reconcile_stale_running_jobs()
        if recovered:
            logger.warning("Marked %s stale running jobs as failed", recovered)
    except Exception:
        logger.exception("Failed to reconcile stale running jobs")


# Import task modules once so tests and shell probes see registered tasks
# immediately after importing app.workers.celery_app.
from app.workers import tasks_dxf as _tasks_dxf  # noqa: E402,F401
from app.workers import tasks_dxf2dwg as _tasks_dxf2dwg  # noqa: E402,F401
from app.workers import tasks_dxf2excel as _tasks_dxf2excel  # noqa: E402,F401
from app.workers import tasks_excel_final as _tasks_excel_final  # noqa: E402,F401
from app.workers import tasks_report as _tasks_report  # noqa: E402,F401
