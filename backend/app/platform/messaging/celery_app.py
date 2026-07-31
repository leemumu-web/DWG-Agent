from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from celery import Celery
from celery.signals import (
    celeryd_init,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
    worker_shutdown,
)
from sqlalchemy import MetaData, Table, delete, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.platform.config.settings import settings
from app.platform.database.session import engine

logger = logging.getLogger(__name__)

SQL_BROKER_MESSAGE_INDEX = "ix_kombu_message_queue_timestamp_id_visible"
SQL_BROKER_SCHEMA_LOCK = "dwg-agent:sql-broker-schema"
SQL_BROKER_SCHEMA_LOCK_TIMEOUT_SECONDS = 60
WORKER_READY_MARKER = Path("/tmp/dwg-celery-ready")

RESERVED_EXECUTION_QUEUES = ("agent", "cad", "dispatch")

JOB_QUEUE_NAMES = (
    "agent",
    "cad",
    "dispatch",
    "dxf",
    "dxf2dwg",
    "dxf2excel",
    "dxf_classification",
    "dxf_split",
    "excel_final",
    "excel_stage2",
    "report",
    "maintenance",
    "remnant_convert",
    "remnant_parse",
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

_worker_ready_callbacks: dict[str, Callable[[], None]] = {}
_worker_signal_callbacks: dict[str, Callable[..., None]] = {}

celery_app = Celery(
    "dwg_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.modules.cad_processing.tasks",
        "app.modules.dxf_classification.tasks",
        "app.modules.dxf_splitting.tasks",
        "app.modules.excel_processing.tasks",
        "app.modules.jobs.tasks",
        "app.modules.operations.daily_archive.tasks",
        "app.modules.operations.storage_reconciliation.tasks",
        "app.modules.operations.control_plane.tasks",
        "app.modules.remnant_inventory.tasks",
        "app.modules.workflows.retention_tasks",
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
        # Keep deterministic transport seams for execution planes whose task
        # implementations are intentionally absent.  These entries route a
        # future published name; they do not register or execute a fake task.
        "app.workers.tasks_agent.*": {"queue": "agent"},
        "app.workers.tasks_cad.*": {"queue": "cad"},
        "app.workers.tasks_dispatch.*": {"queue": "dispatch"},
        "app.workers.tasks_dxf.*": {"queue": "dxf"},
        "app.workers.tasks_dxf2dwg.*": {"queue": "dxf2dwg"},
        "app.workers.tasks_dxf2excel.*": {"queue": "dxf2excel"},
        "app.workers.tasks_dxf_classification.*": {"queue": "dxf_classification"},
        "app.workers.tasks_dxf_split.*": {"queue": "dxf_split"},
        "app.workers.tasks_excel_final.*": {"queue": "excel_final"},
        "app.workers.tasks_excel_stage2.*": {"queue": "excel_stage2"},
        "app.workers.tasks_report.*": {"queue": "report"},
        "app.workers.tasks_maintenance.*": {"queue": "maintenance"},
        "app.workers.tasks_remnant_convert.*": {"queue": "remnant_convert"},
        "app.workers.tasks_remnant_parse.*": {"queue": "remnant_parse"},
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
    if SQL_BROKER_MESSAGE_INDEX in {item["name"] for item in inspector.get_indexes(table_name)}:
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


def _sql_broker_schema_is_ready(db_engine: Engine) -> bool:
    inspector = inspect(db_engine)
    if not inspector.has_table("kombu_queue") or not inspector.has_table("kombu_message"):
        return False
    return SQL_BROKER_MESSAGE_INDEX in {
        item["name"] for item in inspector.get_indexes("kombu_message")
    }


@contextmanager
def _sql_broker_schema_lock(db_engine: Engine) -> Iterator[None]:
    """Serialize Kombu's lazy MySQL DDL across independently started workers."""
    if db_engine.dialect.name != "mysql":
        yield
        return

    parameters = {
        "lock_name": SQL_BROKER_SCHEMA_LOCK,
        "timeout_seconds": SQL_BROKER_SCHEMA_LOCK_TIMEOUT_SECONDS,
    }
    with db_engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            parameters,
        )
        if acquired != 1:
            raise RuntimeError("Timed out acquiring the SQL broker schema lock.")
        try:
            yield
        finally:
            released = connection.scalar(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": SQL_BROKER_SCHEMA_LOCK},
            )
            if released != 1:
                logger.warning("SQL broker schema lock was not owned during release")


def prepare_sql_broker_schema(
    app: Celery = celery_app,
    db_engine: Engine = engine,
) -> bool:
    """Create Kombu tables, close the bootstrap channel, then add claim index."""
    if _sql_broker_schema_is_ready(db_engine):
        return False

    with _sql_broker_schema_lock(db_engine):
        if _sql_broker_schema_is_ready(db_engine):
            return False
        with app.connection_for_write() as connection:
            channel = connection.channel()
            channel_session = None
            try:
                # Channel construction is lazy; declaring a harmless default queue
                # forces Kombu to create and commit its SQL tables on an empty DB.
                channel.queue_declare(queue="default", durable=True)
                channel_session = channel.session
                channel_session.commit()
            finally:
                if channel_session is not None:
                    channel_session.close()
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
    now: datetime | None = None,
) -> int:
    """Delete SQL-transport rows that are certainly no longer consumable.

    Kombu marks a row ``visible=False`` the instant it is reserved for delivery
    (before the child task acks). Deleting such rows blindly on every worker
    start would discard messages whose child task is still running; if that
    child is later lost, ``task_reject_on_worker_lost`` cannot redeliver what
    no longer exists and the job hangs until stale reconciliation.

    The SQL transport cannot distinguish "reserved, unacked" from "acked,
    not yet flushed" through the table alone. Instead, only delete rows whose
    age exceeds a conservative multiple of the stale-job timeout: by then any
    live task has long finished or been reconciled to failed, so the row is
    either an acked leftover (safe to remove) or an orphan from a crashed
    worker whose job was already recovered (removing it changes nothing).
    """
    metadata = MetaData()
    message_table = Table(table_name, metadata, autoload_with=db_engine)
    if "visible" not in message_table.c:
        raise RuntimeError(f"{table_name} has no visible column")
    if "timestamp" not in message_table.c:
        raise RuntimeError(f"{table_name} has no timestamp column")

    cutoff = (now or datetime.now(UTC)) - timedelta(
        seconds=settings.celery_stale_job_timeout_seconds * 2,
    )
    with db_engine.begin() as connection:
        result = connection.execute(
            delete(message_table).where(
                message_table.c.visible.is_(False),
                message_table.c.timestamp < cutoff,
            )
        )
    return result.rowcount or 0


def register_worker_ready_callback(name: str, callback: Callable[[], None]) -> None:
    """Register one idempotently named application maintenance callback."""
    _worker_ready_callbacks[name] = callback


def register_worker_signal_callback(
    name: str,
    callback: Callable[..., None],
) -> None:
    """Register one business observer without importing it into platform."""
    _worker_signal_callbacks[name] = callback


def run_worker_ready_callbacks() -> None:
    """Run application callbacks without hiding one callback's failure."""
    for name, callback in tuple(_worker_ready_callbacks.items()):
        try:
            callback()
        except Exception:
            logger.exception("Worker-ready callback failed: %s", name)


def dispose_inherited_resources(db_engine: Engine = engine) -> None:
    """Drop application DB connections inherited across Celery's fork boundary."""
    from app.platform.storage.factory import clear_storage_backend_cache

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


def _worker_identity(sender=None) -> str:
    request = getattr(sender, "request", None)
    return str(
        getattr(sender, "hostname", None)
        or getattr(request, "hostname", None)
        or os.environ.get("CELERY_WORKER_NODENAME")
        or f"unknown@{socket.gethostname()}:{os.getpid()}"
    )


def _worker_queues() -> list[str]:
    return [
        item.strip() for item in os.environ.get("DWG_WORKER_QUEUE", "").split(",") if item.strip()
    ]


def _emit_worker_signal(
    status: str, event_type: str, sender=None, task_id: str | None = None
) -> None:
    """Notify optional observers without letting them disrupt a Celery task."""
    for name, callback in tuple(_worker_signal_callbacks.items()):
        try:
            callback(
                worker_name=_worker_identity(sender),
                status=status,
                event_type=event_type,
                queues=_worker_queues(),
                concurrency=int(os.environ.get("DWG_WORKER_CONCURRENCY", "1")),
                correlation_id=task_id,
            )
        except Exception:
            logger.exception("Worker signal observer failed: %s", name)


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
            logger.info("Removed %s stale SQL broker rows", removed)
    except Exception:
        logger.exception("Failed to clean stale SQL broker rows")
    run_worker_ready_callbacks()
    update_worker_readiness_marker(True)
    _emit_worker_signal("online", "worker.online", sender)


@worker_shutdown.connect
def _remove_worker_readiness_marker(sender=None, **_kwargs) -> None:
    _emit_worker_signal("stopped", "worker.stopped", sender)
    update_worker_readiness_marker(False)


@task_prerun.connect
def _record_task_start(task_id=None, task=None, **_kwargs) -> None:
    _emit_worker_signal("online", "task.started", sender=task, task_id=task_id)


@task_postrun.connect
def _record_task_finish(task_id=None, task=None, **_kwargs) -> None:
    _emit_worker_signal("online", "task.finished", sender=task, task_id=task_id)


# Load the explicit task registry once so tests, workers and shell probes see
# the same stable public task names immediately after importing this module.
from app.bootstrap.task_registry import load_tasks  # noqa: E402

load_tasks()
