from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_ready

from app.core.config import settings

logger = logging.getLogger(__name__)

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
    },
    # Discard tasks older than 1 hour — prevents workers from processing
    # zombie messages that reference deleted jobs.
    task_default_expires=3600,
    # Reject tasks when the worker process is lost (SIGKILL, OOM) so they
    # are re-queued instead of silently lost.
    task_reject_on_worker_lost=True,
    # Acknowledge tasks only AFTER they complete, not before. Combined with
    # the idempotency guard (job.status == "queued"), this ensures tasks
    # are never silently dropped.
    task_acks_late=True,
    # Process one task at a time in the dxf2excel queue so progress events
    # map to a single job; other queues can be tuned separately.
    worker_prefetch_multiplier=1,
    # ── existing settings ─────────────────────────────────────────────────
    enable_utc=True,
    result_expires=24 * 60 * 60,
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


@worker_ready.connect
def _cleanup_results_on_worker_start(sender=None, **_kwargs) -> None:
    cleanup_expired_task_results(getattr(sender, "app", celery_app))

# Import task modules once so tests and shell probes see registered tasks
# immediately after importing app.workers.celery_app.
from app.workers import tasks_dxf as _tasks_dxf  # noqa: E402,F401
from app.workers import tasks_dxf2dwg as _tasks_dxf2dwg  # noqa: E402,F401
from app.workers import tasks_dxf2excel as _tasks_dxf2excel  # noqa: E402,F401
from app.workers import tasks_excel_final as _tasks_excel_final  # noqa: E402,F401
from app.workers import tasks_report as _tasks_report  # noqa: E402,F401
