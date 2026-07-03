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
        "app.workers.tasks_report",
    ],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_serializer="json",
    task_always_eager=settings.celery_task_always_eager,
    task_default_queue="default",
    task_eager_propagates=True,
    task_routes={
        "app.workers.tasks_agent.*": {"queue": "agent"},
        "app.workers.tasks_dxf.*": {"queue": "dxf"},
        "app.workers.tasks_cad.*": {"queue": "cad"},
        "app.workers.tasks_report.*": {"queue": "report"},
    },
    task_serializer="json",
    task_track_started=True,
    timezone="UTC",
)

# Import task modules once so tests, shell probes, and Flower can see registered
# tasks immediately after importing app.workers.celery_app.
from app.workers import tasks_report as _tasks_report  # noqa: E402,F401
