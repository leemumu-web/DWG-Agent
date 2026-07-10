"""Durable job progress events backed by the ``jobs`` MySQL table.

Workers store the latest event in ``Job.progress_data`` in the same transaction
as the authoritative status/progress fields. SSE readers use a fresh short-lived
session for every poll so MySQL transactions never pin a stale snapshot or occupy
a pool connection while waiting.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.models.job import Job

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_MAX_DURATION = 600.0
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def make_event(
    *,
    type_: str,
    status: str | None = None,
    progress: int | None = None,
    step_name: str | None = None,
    message: str | None = None,
    error_code: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Construct a standard job-progress payload."""
    event: dict[str, Any] = {"type": type_}
    if status is not None:
        event["status"] = status
    if progress is not None:
        event["progress"] = progress
    if step_name is not None:
        event["step_name"] = step_name
    if message is not None:
        event["message"] = message
    if error_code is not None:
        event["error_code"] = error_code
    event.update(extra)
    return event


def publish_job_event(
    db: Session,
    job_id: int,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist the latest event on the job using the caller's transaction.

    The caller owns the commit boundary. This keeps job status, progress and the
    event payload atomic and avoids a second session contending for the same row.
    """
    job = db.get(Job, job_id)
    if job is None:
        return None

    payload = dict(event)
    payload["job_id"] = job.id
    payload["status"] = job.status
    payload["progress"] = job.progress or 0
    if job.error_code:
        payload["error_code"] = job.error_code
    if job.error_message:
        payload["error_message"] = job.error_message
        payload.setdefault("message", job.error_message)

    if job.status == "failed":
        payload["type"] = "error"
    elif job.status in {"succeeded", "cancelled"}:
        payload["type"] = "done"

    job.progress_data = payload
    db.flush()
    return payload


def job_event_from_row(job: Job) -> dict[str, Any]:
    """Build an SSE payload from the current durable job state."""
    payload = dict(job.progress_data or {})
    payload["job_id"] = job.id
    payload["status"] = job.status
    payload["progress"] = job.progress or 0

    if job.error_code:
        payload["error_code"] = job.error_code
    if job.error_message:
        payload["error_message"] = job.error_message
        payload.setdefault("message", job.error_message)
    else:
        payload.setdefault("message", "")

    if job.status == "failed":
        payload["type"] = "error"
    elif job.status in {"succeeded", "cancelled"}:
        payload["type"] = "done"
    else:
        payload.setdefault("type", "status")
    return payload


def _fingerprint(job: Job) -> tuple[str, int, str | None, str | None, str]:
    return (
        job.status,
        job.progress or 0,
        job.error_code,
        job.error_message,
        json.dumps(job.progress_data, ensure_ascii=False, sort_keys=True, default=str),
    )


def job_event_stream(
    session_factory: Callable[[], Session],
    job_id: int,
    *,
    poll_interval: float = _POLL_INTERVAL,
    max_duration: float = _MAX_DURATION,
) -> Iterator[dict[str, Any] | None]:
    """Poll durable job state with one short-lived session per iteration."""
    deadline = time.monotonic() + max_duration
    last_fingerprint: tuple[str, int, str | None, str | None, str] | None = None

    while time.monotonic() < deadline:
        try:
            with session_factory() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                current_fingerprint = _fingerprint(job)
                current_status = job.status
                event = job_event_from_row(job)
        except Exception:
            logger.exception("MySQL job poll failed for job_id=%s", job_id)
            return

        if current_fingerprint != last_fingerprint:
            last_fingerprint = current_fingerprint
            yield event
            if current_status in _TERMINAL_STATUSES:
                return

        if poll_interval > 0:
            time.sleep(poll_interval)
        yield None
