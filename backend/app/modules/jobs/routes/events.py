"""Current-state Server-Sent Event routes backed by MySQL polling."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.identity.interface import CurrentUserForSSE
from app.modules.jobs.access import require_job_read_access
from app.modules.jobs.event_stream import job_event_stream, job_snapshot, jobs_event_stream
from app.modules.jobs.models import Job
from app.platform.config.constants import TASK_DWG_TO_DXF, TASK_DXF_TO_DWG
from app.platform.http.dependencies import get_db
from app.platform.http.exceptions import AppHTTPException, not_found

static_router = APIRouter()
item_router = APIRouter()


@static_router.get("/events/stream")
def get_conversion_events(
    task_type: str,
    file_ids: str,
    current_user: CurrentUserForSSE,
    db: Session = Depends(get_db),
):
    """Stream the latest jobs for an ordered set of conversion source files."""
    if task_type not in {TASK_DWG_TO_DXF, TASK_DXF_TO_DWG}:
        raise AppHTTPException(
            422,
            "INVALID_PARAMS",
            "task_type must be a supported bidirectional CAD conversion.",
        )
    try:
        requested_file_ids = tuple(
            dict.fromkeys(int(value) for value in file_ids.split(",") if value.strip())
        )
    except ValueError as exc:
        raise AppHTTPException(
            422, "INVALID_PARAMS", "file_ids must be comma-separated integers."
        ) from exc
    if not requested_file_ids or len(requested_file_ids) > 200:
        raise AppHTTPException(
            422, "INVALID_PARAMS", "file_ids must contain between 1 and 200 ids."
        )

    candidates = list(
        db.scalars(
            select(Job)
            .where(
                Job.task_type == task_type,
                Job.params_json["file_id"].as_integer().in_(requested_file_ids),
            )
            .order_by(Job.id.desc())
        ).all()
    )
    latest_by_file: dict[int, Job] = {}
    for job in candidates:
        raw_file_id = (job.params_json or {}).get("file_id")
        if isinstance(raw_file_id, int):
            latest_by_file.setdefault(raw_file_id, job)
    jobs = [latest_by_file[file_id] for file_id in requested_file_ids if file_id in latest_by_file]
    if not jobs:
        raise not_found("Job")
    for job in jobs:
        require_job_read_access(db, current_user, job)

    bind = db.get_bind()
    stream_sessions = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    job_ids = [job.id for job in jobs]
    db.rollback()

    def event_stream():
        first = True
        for snapshot in jobs_event_stream(stream_sessions, job_ids):
            if snapshot is None:
                yield ": keepalive\n\n"
                continue
            payload = {
                "type": "snapshot" if first else "update",
                "jobs": snapshot,
            }
            first = False
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@item_router.get("/{job_id}/events")
def get_job_events(
    job_id: int, request: Request, current_user: CurrentUserForSSE, db: Session = Depends(get_db)
):
    """Server-Sent Events 端点：从 MySQL 任务行流式推送进度。

    先发送数据库快照，再以短事务轮询最新状态；空轮次发送 keepalive。
    终态事件后再发送一帧终态快照兜底。

    鉴权：Authorization header 优先；浏览器 EventSource 使用仅限 jobs 路径的
    HttpOnly 短期 Cookie，避免把访问令牌写入 URL 和访问日志。
    """
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)

    terminal = {"succeeded", "failed", "cancelled"}
    initial_snapshot = job_snapshot(db, job_id)
    bind = db.get_bind()
    stream_sessions = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    # Release the request transaction before the long-lived response starts.
    db.rollback()

    def event_stream():
        yield f"data: {json.dumps(initial_snapshot, ensure_ascii=False)}\n\n"
        if initial_snapshot["status"] in terminal:
            return

        stream = job_event_stream(stream_sessions, job_id)
        for event in stream:
            if event is None:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in {"done", "error"}:
                break

        with stream_sessions() as final_db:
            final_snapshot = job_snapshot(final_db, job_id)
        yield f"data: {json.dumps(final_snapshot, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
