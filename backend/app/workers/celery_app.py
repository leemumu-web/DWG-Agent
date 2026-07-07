from __future__ import annotations

from celery import Celery

from app.core.config import settings

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
        "app.workers.tasks_report",
    ],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    # ── queue hygiene (production safety) ──────────────────────────────────
    # Cap each queue at ~1000 messages — older messages are dropped so a
    # runaway producer or stale DB can't fill Redis memory.
    broker_transport_options={
        "max_retries": 3,
        "interval_start": 0.5,
        "interval_step": 0.5,
        "interval_max": 3,
        "master_name": None,  # sentinel-only, ignored in standalone redis
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
    result_serializer="json",
    task_always_eager=settings.celery_task_always_eager,
    task_default_queue="default",
    task_eager_propagates=True,
    task_routes={
        "app.workers.tasks_agent.*": {"queue": "agent"},
        "app.workers.tasks_dxf.*": {"queue": "dxf"},
        "app.workers.tasks_dxf2dwg.*": {"queue": "dxf2dwg"},
        "app.workers.tasks_dxf2excel.*": {"queue": "dxf2excel"},
        "app.workers.tasks_cad.*": {"queue": "cad"},
        "app.workers.tasks_report.*": {"queue": "report"},
    },
    task_serializer="json",
    task_track_started=True,
    timezone="UTC",
)

# Import task modules once so tests, shell probes, and Flower can see registered
# tasks immediately after importing app.workers.celery_app.
from app.workers import tasks_dxf as _tasks_dxf  # noqa: E402,F401
from app.workers import tasks_dxf2dwg as _tasks_dxf2dwg  # noqa: E402,F401
from app.workers import tasks_dxf2excel as _tasks_dxf2excel  # noqa: E402,F401
from app.workers import tasks_report as _tasks_report  # noqa: E402,F401
