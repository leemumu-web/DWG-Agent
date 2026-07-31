"""MySQL and object-storage registration for a converted DWG artifact."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.modules.cad_processing.dxf_to_dwg.contracts import (
    ALGORITHM_VERSION,
    DWG_CONTENT_TYPE,
    DWG_EXTENSION,
    ERROR_CODE_DWG_FAILED,
)
from app.modules.cad_processing.execution import (
    CadProcessingError,
    add_job_step,
    mark_job_failed,
)
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
    complete_job_attempt,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    STEP_PERSIST_DWG,
    TASK_DXF_TO_DWG,
)
from app.platform.config.settings import settings
from app.platform.time import business_now

logger = logging.getLogger(__name__)


def persist_dwg_conversion_result(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    source_file_id: int,
    source_path: Path,
    source_stats: dict,
    output_version: str,
    result: Any,
    worker_name: str,
) -> bool:
    """Register one DWG only while its claimed Job attempt is still active."""
    job = db.get(Job, job_id, populate_existing=True)
    if job is None or job.status != JOB_RUNNING or job.attempt != attempt:
        db.rollback()
        return False

    persist_started = business_now()
    dwg_path = result.target
    if not dwg_path.is_file():
        mark_job_failed(
            db,
            job_id,
            attempt,
            CadProcessingError(f"DWG 产物未生成: {dwg_path}"),
            error_code=ERROR_CODE_DWG_FAILED,
            logger=logger,
        )
        return False

    dwg_bytes = dwg_path.read_bytes()
    source_file = db.get(StoredFile, source_file_id)
    source_base = source_file.original_name if source_file else source_path.name
    source_base = sanitize_filename(source_base)
    source_stem = source_base.rsplit(".", 1)[0] if "." in source_base else source_base
    storage_key = f"jobs/{job.id}/{uuid4().hex}{DWG_EXTENSION}"
    original_name = f"{source_stem}{DWG_EXTENSION}"
    transfer_uid = prepare_generated_file_transfer(
        db,
        actor_user_id=job.created_by,
        request_id=f"job:{job.id}:attempt:{attempt}:dwg",
        batch_ref=source_file.batch_name if source_file else None,
        bucket=settings.minio_bucket_derived,
        storage_key=storage_key,
        original_name=original_name,
        expected_bytes=len(dwg_bytes),
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

    dwg_file = save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_derived,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=DWG_EXTENSION,
        content_type=DWG_CONTENT_TYPE,
        payload=dwg_bytes,
        uploaded_by=job.created_by,
        batch_name=source_file.batch_name if source_file else None,
        transfer_uid=transfer_uid,
    )
    complete_transfer_in_transaction(
        db,
        transfer_uid,
        file_id=dwg_file.id,
        bucket=dwg_file.bucket,
        storage_key=dwg_file.storage_key,
        original_name=dwg_file.original_name,
        transferred_bytes=dwg_file.size_bytes,
    )

    analysis = AnalysisResult(
        job_id=job.id,
        drawing_id=job.drawing_id,
        result_type=TASK_DXF_TO_DWG,
        result_json={
            "source": "dxf2dwg_open_source",
            "job_id": job.id,
            "task_type": TASK_DXF_TO_DWG,
            "source_file_id": source_file_id,
            "dwg_file_id": dwg_file.id,
            "convert_result": result.to_dict(),
            "source_dxf_stats": source_stats,
        },
        confidence=Decimal("1.0000"),
        result_file_id=dwg_file.id,
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
        STEP_PERSIST_DWG,
        worker_name,
        "succeeded",
        input_json={"dwg_size": len(dwg_bytes)},
        output_json={
            "dwg_file_id": dwg_file.id,
            "analysis_result_id": analysis.id,
            "source_entity_counts": source_stats.get("entity_counts", {}),
            "source_total_entities": source_stats.get("total_entities", 0),
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
            progress=100,
            step_name=STEP_PERSIST_DWG,
            message="DXF→DWG 转换完成",
        ),
    )
    return completed_job is not None
