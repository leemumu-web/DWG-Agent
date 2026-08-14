"""Persistent latest-state Job progress backed by the ``jobs`` MySQL row.

Workers store the latest event in ``Job.progress_data`` in the same transaction
as the authoritative status/progress fields. SSE readers use a fresh short-lived
session for every poll so MySQL transactions never pin a stale snapshot or occupy
a pool connection while waiting. This is not a numbered event log and cannot
replay intermediate events after a disconnect.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.jobs.models import Job, JobStep

logger = logging.getLogger(__name__)

# SSE 轮询设计参数：2.0s 平衡实时性与 MySQL 轮询压力；600s 是 SSE 会话
# 硬上限（短会话轮询 + 有界 SSE）。集合流另有 0.5s 的首帧快速呈现间隔。
# 调整时需评估部署规模下的轮询写放大（见 presentation.INPUT_BATCH_SYNC_LIMIT）。
_POLL_INTERVAL = 2.0
_MAX_DURATION = 600.0
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def job_snapshot(db: Session, job_id: int) -> dict[str, Any]:
    """Read the authoritative Job and current-attempt Step snapshot."""
    job = db.get(Job, job_id)
    if job is None:
        return {"type": "snapshot", "job_id": job_id, "status": "unknown"}
    steps = list(
        db.scalars(
            select(JobStep)
            .where(JobStep.job_id == job_id, JobStep.attempt == job.attempt)
            .order_by(JobStep.id)
        ).all()
    )
    return {
        "type": "snapshot",
        "job_id": job_id,
        "status": job.status,
        "attempt": job.attempt,
        "progress": job.progress,
        "pipeline": job.pipeline,
        "task_type": job.task_type,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "progress_data": job.progress_data,
        "steps": [
            {
                "attempt": step.attempt,
                "step_name": step.step_name,
                "status": step.status,
                "error_message": step.error_message,
            }
            for step in steps
        ],
    }


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
    payload["attempt"] = job.attempt
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
    payload["attempt"] = job.attempt
    payload["task_type"] = job.task_type
    payload["pipeline"] = job.pipeline
    payload["params_json"] = job.params_json
    payload["progress_data"] = job.progress_data

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


def _fingerprint(job: Job) -> tuple[str, int, int, str | None, str | None, str]:
    return (
        job.status,
        job.attempt,
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
    last_fingerprint: tuple[str, int, int, str | None, str | None, str] | None = None

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


def jobs_event_stream(
    session_factory: Callable[[], Session],
    job_ids: Sequence[int],
    *,
    poll_interval: float = 0.5,
    max_duration: float = _MAX_DURATION,
) -> Iterator[list[dict[str, Any]] | None]:
    """Poll an ordered Job set with one short-lived session per iteration."""
    requested_ids = tuple(dict.fromkeys(job_ids))
    if not requested_ids:
        return
    deadline = time.monotonic() + max_duration
    last_fingerprint: (
        tuple[tuple[int, tuple[str, int, int, str | None, str | None, str]], ...] | None
    ) = None

    while time.monotonic() < deadline:
        try:
            with session_factory() as db:
                rows = list(db.scalars(select(Job).where(Job.id.in_(requested_ids))).all())
                by_id = {job.id: job for job in rows}
                ordered = [by_id[job_id] for job_id in requested_ids if job_id in by_id]
                current_fingerprint = tuple((job.id, _fingerprint(job)) for job in ordered)
                snapshot = [job_event_from_row(job) for job in ordered]
                all_terminal = len(ordered) == len(requested_ids) and all(
                    job.status in _TERMINAL_STATUSES for job in ordered
                )
        except Exception:
            logger.exception("MySQL job-set poll failed for job_ids=%s", requested_ids)
            return

        if current_fingerprint != last_fingerprint:
            last_fingerprint = current_fingerprint
            yield snapshot
            if all_terminal:
                return

        if poll_interval > 0:
            time.sleep(poll_interval)
        yield None
