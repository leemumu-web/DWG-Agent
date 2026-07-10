from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import TASK_EXCEL_FINAL
from app.models.excel_final import ExcelFinalBatch, ExcelFinalPart
from app.models.file import StoredFile
from app.models.job import Job
from app.services.excel_final_service import _replace_batch_for_job


def test_retry_replaces_previously_committed_excel_batch(db: Session):
    source = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/source.xls",
        original_name="source.xls",
        file_ext=".xls",
        content_type="application/vnd.ms-excel",
        size_bytes=10,
        sha256="a" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="running",
        priority=0,
        progress=60,
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.flush()
    old_batch = ExcelFinalBatch(
        job_id=job.id,
        file_id=source.id,
        source_type="tsv",
        source_name="old.xls",
        part_count=1,
        component_count=0,
    )
    db.add(old_batch)
    db.flush()
    old_batch_id = old_batch.id
    db.add(ExcelFinalPart(batch_id=old_batch.id, seq=1, part_no="OLD"))
    db.commit()

    replacement = _replace_batch_for_job(
        db,
        job_id=job.id,
        file_id=source.id,
        source_type="tsv",
        source_name="new.xls",
    )
    db.commit()

    assert replacement.id != old_batch_id
    assert replacement.source_name == "new.xls"
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExcelFinalBatch)
            .where(ExcelFinalBatch.job_id == job.id)
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExcelFinalPart)
            .where(ExcelFinalPart.batch_id == old_batch_id)
        )
        == 0
    )
