from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.excel_final import ExcelFinalProcessError
from app.models.excel_final import ExcelFinalBatch, ExcelFinalPart
from app.models.job import Job
from app.modules.files.interface import StoredFile
from app.platform.config.constants import TASK_EXCEL_FINAL
from app.services.excel_final_service import (
    _mark_job_failed,
    _replace_batch_for_job,
    run_excel_final_processing,
)


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
            .where(ExcelFinalPart.batch_id == replacement.id)
        )
        == 0
    )


def test_current_attempt_failure_removes_provisional_batch(db: Session):
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="running",
        attempt=2,
        progress=75,
        params_json={},
    )
    db.add(job)
    db.flush()
    db.add(
        ExcelFinalBatch(
            job_id=job.id,
            source_type="init_table",
            source_name="partial.xlsx",
        )
    )
    db.commit()

    marked = _mark_job_failed(db, job.id, 2, RuntimeError("persist failed"))

    db.expire_all()
    assert marked is True
    assert db.get(Job, job.id).status == "failed"
    assert db.scalar(
        select(func.count())
        .select_from(ExcelFinalBatch)
        .where(ExcelFinalBatch.job_id == job.id)
    ) == 0


def test_stale_attempt_failure_cannot_delete_new_attempt_batch(db: Session):
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="running",
        attempt=3,
        progress=75,
        params_json={},
    )
    db.add(job)
    db.flush()
    db.add(
        ExcelFinalBatch(
            job_id=job.id,
            source_type="init_table",
            source_name="current.xlsx",
        )
    )
    db.commit()

    marked = _mark_job_failed(db, job.id, 2, RuntimeError("old worker failed"))

    db.expire_all()
    assert marked is False
    assert db.get(Job, job.id).status == "running"
    assert db.scalar(
        select(func.count())
        .select_from(ExcelFinalBatch)
        .where(ExcelFinalBatch.job_id == job.id)
    ) == 1


def test_pipeline_failure_uses_one_session_and_commits_failed_step(
    db: Session,
    monkeypatch,
    tmp_path,
):
    from app.services import excel_final_service as service

    source = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/invalid.xls",
        original_name="invalid.xls",
        file_ext=".xls",
        content_type="application/vnd.ms-excel",
        size_bytes=7,
        sha256="b" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=2,
        progress=0,
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.commit()

    source_path = tmp_path / "invalid.xls"
    source_path.write_bytes(b"invalid")

    def fake_stage(worker_db: Session, file_id: int, _work_dir):
        return source_path, worker_db.get(StoredFile, file_id)

    monkeypatch.setattr(service, "_stage_excel_source", fake_stage)
    monkeypatch.setattr(service, "_detect_format", lambda _path: "tsv")
    monkeypatch.setattr(
        service,
        "run_excel_final_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExcelFinalProcessError("safe processing failure")
        ),
    )

    original_session_factory = service.SessionLocal
    session_count = 0

    def counted_session_factory():
        nonlocal session_count
        session_count += 1
        return original_session_factory()

    monkeypatch.setattr(service, "SessionLocal", counted_session_factory)

    run_excel_final_processing(job.id, worker_name="failure-worker", expected_attempt=2)

    db.expire_all()
    persisted = db.get(Job, job.id)
    steps = list(
        db.scalars(select(service.JobStep).where(service.JobStep.job_id == job.id)).all()
    )
    assert session_count == 1
    assert persisted.status == "failed"
    assert persisted.error_message == "流水线处理失败: safe processing failure"
    assert [(step.attempt, step.step_name, step.status) for step in steps] == [
        (2, "download_excel_source", "succeeded"),
        (2, "run_excel_final_pipeline", "failed"),
    ]
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExcelFinalPart)
            .where(ExcelFinalPart.part_no == "OLD")
        )
        == 0
    )
