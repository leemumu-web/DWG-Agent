from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from celery import Celery
from celery.signals import celeryd_init, worker_process_init, worker_ready, worker_shutdown
from sqlalchemy import MetaData, Table, delete, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.constants import JOB_FAILED, JOB_RUNNING
from app.db.session import SessionLocal, engine
from app.models.excel_final import ExcelFinalBatch
from app.models.job import Job

logger = logging.getLogger(__name__)

SQL_BROKER_MESSAGE_INDEX = "ix_kombu_message_queue_timestamp_id_visible"
WORKER_READY_MARKER = Path("/tmp/dwg-celery-ready")

JOB_QUEUE_NAMES = (
    "agent",
    "cad",
    "dxf",
    "dxf2dwg",
    "dxf2excel",
    "excel_final",
    "report",
)

_celery_engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": settings.db_pool_recycle_seconds,
    "pool_size": 1,
    "max_overflow": 1,
    "pool_timeout": settings.db_pool_timeout_seconds,
    "pool_use_lifo": True,
    # Kombu claims rows with SELECT ... FOR UPDATE. READ COMMITTED avoids
    # InnoDB next-key locks spanning unrelated queues under concurrent workers.
    "isolation_level": "READ COMMITTED",
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


def ensure_sql_broker_message_index(
    db_engine: Engine = engine,
    *,
    table_name: str = "kombu_message",
) -> bool:
    """Ensure Kombu can claim the oldest message without scanning other queues."""
    inspector = inspect(db_engine)
    if not inspector.has_table(table_name):
        return False
    if SQL_BROKER_MESSAGE_INDEX in {
        item["name"] for item in inspector.get_indexes(table_name)
    }:
        return False

    statement = text(
        f"CREATE INDEX {SQL_BROKER_MESSAGE_INDEX} ON {table_name} "
        "(queue_id, timestamp, id, visible)"
    )
    try:
        with db_engine.begin() as connection:
            connection.execute(statement)
    except DBAPIError:
        # Multiple workers may reach worker_ready together. Treat another
        # process winning the DDL race as success, but surface real failures.
        if SQL_BROKER_MESSAGE_INDEX not in {
            item["name"] for item in inspect(db_engine).get_indexes(table_name)
        }:
            raise
        return False
    return True


def prepare_sql_broker_schema(
    app: Celery = celery_app,
    db_engine: Engine = engine,
) -> bool:
    """Create Kombu tables, close the bootstrap channel, then add claim index."""
    with app.connection_for_write() as connection:
        channel = connection.channel()
        try:
            # Channel construction is lazy; declaring a harmless default queue
            # forces Kombu to create and commit its SQL tables on an empty DB.
            channel.queue_declare(queue="default", durable=True)
        finally:
            channel.close()
    return ensure_sql_broker_message_index(db_engine)


def purge_queued_job_messages(
    app: Celery = celery_app,
    *,
    queue_names: tuple[str, ...] = JOB_QUEUE_NAMES,
) -> tuple[dict[str, int], dict[str, str]]:
    """Purge ready SQL-transport messages queue by queue.

    Celery's ``Control.purge`` accepts only a connection and sees only declared
    queues. Routes create this application's queues dynamically, so use the
    transport channel directly and report partial failures to the caller.
    """
    purged: dict[str, int] = {}
    errors: dict[str, str] = {}
    with app.connection_for_write() as connection:
        channel = connection.channel()
        try:
            for queue_name in queue_names:
                try:
                    purged[queue_name] = int(channel.queue_purge(queue_name) or 0)
                except Exception as exc:
                    errors[queue_name] = str(exc) or exc.__class__.__name__
        finally:
            channel.close()
    return purged, errors


def summarize_job_execution(
    job_id: int,
    pipeline: str,
    *,
    session_factory: sessionmaker | None = None,
) -> dict[str, int | str]:
    """Build the Celery result payload from the authoritative MySQL job row."""
    factory = session_factory or SessionLocal
    with factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {
                "job_id": job_id,
                "pipeline": pipeline,
                "status": "missing",
                "attempt": 0,
            }
        return {
            "job_id": job.id,
            "pipeline": pipeline,
            "status": job.status,
            "attempt": job.attempt,
        }


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
            select(Job.id, Job.progress, Job.attempt).where(
                Job.status == JOB_RUNNING,
                Job.updated_at < cutoff,
            )
        ).all()
        for job_id, progress, attempt in candidates:
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
                "attempt": attempt,
            }
            result = db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JOB_RUNNING,
                    Job.attempt == attempt,
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
            updated = result.rowcount or 0
            if updated:
                db.execute(
                    delete(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job_id)
                )
            recovered += updated
        db.commit()
    return recovered


def dispose_inherited_resources(db_engine: Engine = engine) -> None:
    """Drop application DB connections inherited across Celery's fork boundary."""
    from app.services.storage_service import clear_storage_backend_cache

    db_engine.dispose(close=False)
    clear_storage_backend_cache()


def update_worker_readiness_marker(
    ready: bool,
    marker: Path = WORKER_READY_MARKER,
) -> None:
    if ready:
        marker.write_text("ready\n", encoding="ascii")
    else:
        marker.unlink(missing_ok=True)


@worker_process_init.connect
def _dispose_resources_in_worker_child(**_kwargs) -> None:
    dispose_inherited_resources()


@celeryd_init.connect
def _prepare_mysql_broker_before_consumer(**_kwargs) -> None:
    update_worker_readiness_marker(False)
    try:
        if prepare_sql_broker_schema():
            logger.info("Created SQL broker queue-ordering index")
    except Exception:
        logger.exception("Failed to prepare SQL broker schema")


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
    update_worker_readiness_marker(True)


@worker_shutdown.connect
def _remove_worker_readiness_marker(**_kwargs) -> None:
    update_worker_readiness_marker(False)


# Import task modules once so tests and shell probes see registered tasks
# immediately after importing app.workers.celery_app.
from app.workers import tasks_dxf as _tasks_dxf  # noqa: E402,F401
from app.workers import tasks_dxf2dwg as _tasks_dxf2dwg  # noqa: E402,F401
from app.workers import tasks_dxf2excel as _tasks_dxf2excel  # noqa: E402,F401
from app.workers import tasks_excel_final as _tasks_excel_final  # noqa: E402,F401
from app.workers import tasks_report as _tasks_report  # noqa: E402,F401
