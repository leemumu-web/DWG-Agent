"""Job creation, pipeline selection and request-key idempotency."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.jobs.event_stream import make_event, publish_job_event
from app.modules.jobs.models import Job
from app.modules.jobs.schemas import JobCreate
from app.modules.projects.interface import Drawing
from app.platform.config.constants import (
    JOB_QUEUED,
    PIPELINE_DXF,
    PIPELINE_DXF2DWG,
    PIPELINE_DXF2EXCEL,
    PIPELINE_EXCEL_FINAL,
    PIPELINE_EXCEL_STAGE2,
    PIPELINE_REMNANT_CONVERT,
    PIPELINE_REMNANT_PARSE,
    PIPELINE_STEEL_DXF_CLASSIFIER,
    PIPELINE_STEEL_DXF_SPLIT,
    PIPELINE_STUB,
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
    TASK_DXF_TO_EXCEL,
    TASK_EXCEL_FINAL,
    TASK_EXCEL_STAGE2,
    TASK_REMNANT_CONVERT,
    TASK_REMNANT_PARSE,
    TASK_STEEL_DXF_CLASSIFICATION,
    TASK_STEEL_DXF_SPLIT,
)
from app.platform.http.exceptions import AppHTTPException


def _pipeline_for(task_type: str) -> str:
    """返回 task_type 对应的 pipeline 标识（spec §16.3 管线选择）。"""
    if task_type == TASK_DWG_TO_DXF:
        return PIPELINE_DXF
    if task_type == TASK_DXF_TO_DWG:
        return PIPELINE_DXF2DWG
    if task_type == TASK_DXF_TO_EXCEL:
        return PIPELINE_DXF2EXCEL
    if task_type == TASK_EXCEL_FINAL:
        return PIPELINE_EXCEL_FINAL
    if task_type == TASK_EXCEL_STAGE2:
        return PIPELINE_EXCEL_STAGE2
    if task_type == TASK_STEEL_DXF_CLASSIFICATION:
        return PIPELINE_STEEL_DXF_CLASSIFIER
    if task_type == TASK_STEEL_DXF_SPLIT:
        return PIPELINE_STEEL_DXF_SPLIT
    if task_type == TASK_REMNANT_CONVERT:
        return PIPELINE_REMNANT_CONVERT
    if task_type == TASK_REMNANT_PARSE:
        return PIPELINE_REMNANT_PARSE
    return PIPELINE_STUB


def create_job(
    db: Session,
    payload: JobCreate,
    created_by: int | None,
    *,
    request_key: str | None = None,
) -> Job:
    project_id = payload.project_id
    if project_id is None and payload.drawing_id is not None:
        drawing = db.get(Drawing, payload.drawing_id)
        if drawing:
            project_id = drawing.project_id
    job = Job(
        project_id=project_id,
        drawing_id=payload.drawing_id,
        created_by=created_by,
        task_type=payload.task_type,
        request_key=request_key,
        precision_level=payload.precision_level,
        pipeline=_pipeline_for(payload.task_type),
        status=JOB_QUEUED,
        attempt=1,
        progress=0,
        params_json=payload.params,
    )
    db.add(job)
    db.flush()
    publish_job_event(
        db,
        job.id,
        make_event(type_="status", status=JOB_QUEUED, progress=0, message="任务已入队"),
    )
    return job


def create_conversion_jobs(
    db: Session,
    *,
    task_type: str,
    file_ids: list[int],
    precision_level: str,
    created_by: int,
) -> list[Job]:
    """Validate all sources, then create one ordered Job per unique file ID."""
    from app.modules.files.interface import StoredFile

    expected_ext = ".dwg" if task_type == TASK_DWG_TO_DXF else ".dxf"
    unique_ids = list(dict.fromkeys(file_ids))
    sources: list[StoredFile] = []
    for file_id in unique_ids:
        stored = db.get(StoredFile, file_id)
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(404, "FILE_NOT_FOUND", "File not found.")
        if stored.file_ext.lower() != expected_ext:
            raise AppHTTPException(
                422,
                "INVALID_CONVERSION_SOURCE",
                f"{task_type} requires {expected_ext} source files.",
                {"file_id": file_id, "file_ext": stored.file_ext},
            )
        sources.append(stored)

    jobs: list[Job] = []
    for stored in sources:
        jobs.append(
            create_job(
                db,
                JobCreate(
                    task_type=task_type,
                    precision_level=precision_level,
                    params={"file_id": stored.id, "batch_name": stored.batch_name},
                ),
                created_by=created_by,
            )
        )
    return jobs


def _require_matching_idempotent_job(job: Job, payload: JobCreate) -> None:
    if (
        job.drawing_id != payload.drawing_id
        or job.project_id != payload.project_id
        or job.precision_level != payload.precision_level
        or job.params_json != payload.params
    ):
        raise AppHTTPException(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "The idempotency key was already used with different parameters.",
        )


def create_or_reuse_job(
    db: Session,
    payload: JobCreate,
    *,
    created_by: int,
    request_key: str | None,
) -> tuple[Job, bool]:
    """Create one logical request or return its already committed Job.

    The pre-read handles ordinary HTTP replays. The unique constraint plus
    savepoint handles two processes that race between the pre-read and insert
    without rolling back unrelated work in the caller's outer transaction.
    """
    if request_key is None:
        return create_job(db, payload, created_by), False

    conditions = (
        Job.created_by == created_by,
        Job.task_type == payload.task_type,
        Job.request_key == request_key,
    )
    existing = db.scalar(select(Job).where(*conditions))
    if existing is not None:
        _require_matching_idempotent_job(existing, payload)
        return existing, True

    try:
        with db.begin_nested():
            job = create_job(
                db,
                payload,
                created_by,
                request_key=request_key,
            )
    except IntegrityError:
        # Under MySQL REPEATABLE READ the ordinary pre-read fixes an older
        # consistent snapshot. After the unique-key loser rolls back its
        # savepoint, a locking current read is required to see the winner that
        # committed while the INSERT was waiting on the unique index.
        existing = db.scalar(select(Job).where(*conditions).with_for_update())
        if existing is None:
            raise
        _require_matching_idempotent_job(existing, payload)
        return existing, True
    return job, False
