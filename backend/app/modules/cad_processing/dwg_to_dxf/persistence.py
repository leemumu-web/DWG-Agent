"""MySQL and object-storage registration for a converted DXF artifact."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.modules.cad_processing.dwg_to_dxf.contracts import (
    ALGORITHM_VERSION,
    DXF_CONTENT_TYPE,
    DXF_EXTENSION,
    ERROR_CODE_DXF_FAILED,
)
from app.modules.cad_processing.dwg_to_dxf.progress import (
    COMPLETED,
    PERSISTING,
    phase_data,
    phase_event,
    safe_convert_result_metadata,
    safe_failure_message,
)
from app.modules.cad_processing.execution import (
    CadProcessingError,
    add_job_step,
    mark_job_failed,
)
from app.modules.cad_processing.statistics import _count_dxf_stats, dxf_entity_summary
from app.modules.files.interface import (
    StoredFile,
    complete_transfer_in_transaction,
    prepare_generated_file_transfer,
    sanitize_filename,
    save_bytes_as_file,
    session_factory_for,
    settle_transfer,
)
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    commit_job_progress,
    complete_job_attempt,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    STEP_PERSIST_DXF,
    TASK_DWG_TO_DXF,
)
from app.platform.config.settings import settings
from app.platform.time import business_now

logger = logging.getLogger(__name__)


def persist_dxf_conversion_result(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    source_file_id: int,
    source_path: Path,
    output_version: str,
    result: Any,
    worker_name: str,
) -> bool:
    """Register one DXF only while its claimed Job attempt is still active."""
    job = db.get(Job, job_id, populate_existing=True)
    if job is None or job.status != JOB_RUNNING or job.attempt != attempt:
        db.rollback()
        return False

    persist_started = business_now()
    dxf_path = result.target
    if not dxf_path.is_file():
        mark_job_failed(
            db,
            job_id,
            attempt,
            CadProcessingError(safe_failure_message(ERROR_CODE_DXF_FAILED)),
            error_code=ERROR_CODE_DXF_FAILED,
            logger=logger,
        )
        return False

    dxf_bytes = dxf_path.read_bytes()
    dxf_stats = _count_dxf_stats(dxf_path)
    logger.info("DXF conversion stats for job %s: %s", job_id, dxf_entity_summary(dxf_stats))
    job = commit_job_progress(
        db,
        job_id,
        attempt=attempt,
        progress=PERSISTING.progress,
        event=phase_event(
            PERSISTING,
            step_name=STEP_PERSIST_DXF,
            output_size_bytes=len(dxf_bytes),
            total_entities=dxf_stats.get("total_entities", 0),
        ),
    )
    if job is None:
        return False
    source_file = db.get(StoredFile, source_file_id)
    source_base = source_file.original_name if source_file else source_path.name
    source_base = sanitize_filename(source_base)
    source_stem = source_base.rsplit(".", 1)[0] if "." in source_base else source_base
    storage_key = f"jobs/{job.id}/{uuid4().hex}{DXF_EXTENSION}"
    original_name = f"{source_stem}{DXF_EXTENSION}"
    transfer_uid = prepare_generated_file_transfer(
        db,
        actor_user_id=job.created_by,
        request_id=f"job:{job.id}:attempt:{attempt}:dxf",
        batch_ref=source_file.batch_name if source_file else None,
        bucket=settings.minio_bucket_dxf_derived,
        storage_key=storage_key,
        original_name=original_name,
        expected_bytes=len(dxf_bytes),
    )

    job = db.get(Job, job_id, populate_existing=True)
    if job is None or job.status != JOB_RUNNING or job.attempt != attempt:
        db.rollback()
        settle_transfer(
            session_factory_for(db),
            transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code="JOB_ATTEMPT_INACTIVE",
            error_message="Job attempt changed before generated file persistence.",
        )
        return False

    dxf_file = save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_dxf_derived,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=DXF_EXTENSION,
        content_type=DXF_CONTENT_TYPE,
        payload=dxf_bytes,
        uploaded_by=job.created_by,
        batch_name=source_file.batch_name if source_file else None,
        transfer_uid=transfer_uid,
    )
    complete_transfer_in_transaction(
        db,
        transfer_uid,
        file_id=dxf_file.id,
        bucket=dxf_file.bucket,
        storage_key=dxf_file.storage_key,
        original_name=dxf_file.original_name,
        transferred_bytes=dxf_file.size_bytes,
    )

    analysis = AnalysisResult(
        job_id=job.id,
        drawing_id=job.drawing_id,
        result_type=TASK_DWG_TO_DXF,
        result_json={
            "source": "dxf_open_source",
            "job_id": job.id,
            "task_type": TASK_DWG_TO_DXF,
            "source_file_id": source_file_id,
            "dxf_file_id": dxf_file.id,
            "convert_result": safe_convert_result_metadata(result),
            "dxf_stats": dxf_stats,
        },
        confidence=Decimal("1.0000"),
        result_file_id=dxf_file.id,
        algorithm_version=ALGORITHM_VERSION,
        tool_version=output_version,
        status="succeeded",
    )
    db.add(analysis)
    db.flush()
    add_job_step(
        db,
        job_id,
        attempt,
        STEP_PERSIST_DXF,
        worker_name,
        "succeeded",
        input_json={"dxf_size": len(dxf_bytes)},
        output_json={
            "dxf_file_id": dxf_file.id,
            "analysis_result_id": analysis.id,
            "entity_counts": dxf_stats.get("entity_counts", {}),
            "total_entities": dxf_stats.get("total_entities", 0),
        },
        started_at=persist_started,
    )
    completed_job = complete_job_attempt(
        db,
        job_id,
        attempt=attempt,
        event=make_event(
            type_="done",
            status=JOB_SUCCEEDED,
            progress=COMPLETED.progress,
            step_name=STEP_PERSIST_DXF,
            message=COMPLETED.message,
            **phase_data(COMPLETED),
        ),
    )
    return completed_job is not None
