from __future__ import annotations

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.excel_processing.execution import (
    _exception_message,
    _mark_job_failed,
    run_excel_final_processing,
)
from app.modules.excel_processing.models import ExcelFinalBatch, ExcelFinalPart
from app.modules.excel_processing.persistence import replace_batch_for_job
from app.modules.excel_processing.presentation import process_status
from app.modules.excel_processing.schemas import (
    ExcelInputFailure,
    ExcelInputIssue,
    ExcelStage1Inspection,
)
from app.modules.excel_processing.stage_adapter import (
    ExcelFinalInputError,
    ExcelFinalProcessError,
    ExcelFinalProcessResult,
)
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import Job
from app.platform.config.constants import TASK_EXCEL_FINAL


def _allow_worker_preflight(monkeypatch, service, source_format: str) -> None:
    monkeypatch.setattr(
        service,
        "inspect_excel_stage1_path",
        lambda _path: ExcelStage1Inspection(
            protocol_version=1,
            input_contract_version=1,
            source_format=source_format,
            sheet_name="原表",
            header_row=1,
            part_count=1,
            component_count=1,
        ),
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

    replacement = replace_batch_for_job(
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
    from app.modules.excel_processing import execution as service

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

    monkeypatch.setattr(service, "stage_excel_source", fake_stage)
    _allow_worker_preflight(monkeypatch, service, "fixed_width_tekla_text")
    monkeypatch.setattr(
        service,
        "run_excel_final_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExcelFinalProcessError("secret-host/private/path")
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
    assert persisted.error_message == "流水线处理失败；请检查输入文件和处理报告"
    assert [(step.attempt, step.step_name, step.status) for step in steps] == [
        (2, "download_excel_source", "succeeded"),
        (2, "run_excel_final_pipeline", "failed"),
    ]
    assert all("secret-host" not in (step.error_message or "") for step in steps)
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExcelFinalPart)
            .where(ExcelFinalPart.part_no == "OLD")
        )
        == 0
    )


def test_worker_revalidation_persists_structured_input_failure(
    db: Session,
    monkeypatch,
    tmp_path,
):
    from app.modules.excel_processing import execution as service

    source = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/changed.xlsx",
        original_name="changed.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
        attempt=1,
        progress=0,
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.commit()
    source_path = tmp_path / "changed.xlsx"
    source_path.write_bytes(b"changed")
    failure = ExcelInputFailure(
        code="EXCEL_INPUT_OBJECT_CHANGED",
        message="Excel 文件内容已发生变化。",
        action="请重新上传文件并重新冻结输入后再运行。",
        contract_version=1,
        issues=(
            ExcelInputIssue(
                sheet=None,
                row=None,
                column=None,
                field=None,
                value=None,
                reason="checksum_mismatch",
            ),
        ),
        sheets=(),
        meta={
            "issue_count": 1,
            "issues_truncated": False,
            "sheet_count": 0,
            "sheets_truncated": False,
        },
    )

    monkeypatch.setattr(
        service,
        "stage_excel_source",
        lambda worker_db, file_id, _work_dir: (
            source_path,
            worker_db.get(StoredFile, file_id),
        ),
    )
    _allow_worker_preflight(monkeypatch, service, "standard_workbook")
    monkeypatch.setattr(
        service,
        "inspect_excel_stage1_path",
        lambda _path: (_ for _ in ()).throw(ExcelFinalInputError(failure)),
        raising=False,
    )
    monkeypatch.setattr(
        service,
        "run_excel_final_pipeline",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid worker input must not start the processing pipeline"
        ),
    )

    run_excel_final_processing(job.id, expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    assert persisted.status == "failed"
    assert persisted.error_code == "EXCEL_INPUT_OBJECT_CHANGED"
    assert persisted.error_message == failure.message
    assert persisted.progress_data["failure"] == failure.as_dict()
    assert persisted.progress_data["message"] == failure.message
    status_payload = process_status(
        persisted,
        batch=None,
        result_file_id=None,
    )
    assert status_payload["failure"] == failure.as_dict()
    steps = list(
        db.scalars(select(service.JobStep).where(service.JobStep.job_id == job.id))
    )
    assert [(step.step_name, step.status) for step in steps] == [
        ("download_excel_source", "succeeded"),
        ("run_excel_final_pipeline", "failed"),
    ]


def test_database_import_failure_never_discloses_connection_details(
    db: Session,
    monkeypatch,
    tmp_path,
    caplog,
):
    from app.modules.excel_processing import execution as service

    source = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/import-failure.xlsx",
        original_name="import-failure.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=10,
        sha256="d" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        progress=0,
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.commit()
    source_path = tmp_path / "import-failure.xlsx"
    source_path.write_bytes(b"source")

    monkeypatch.setattr(
        service,
        "stage_excel_source",
        lambda worker_db, file_id, _work_dir: (
            source_path,
            worker_db.get(StoredFile, file_id),
        ),
    )
    _allow_worker_preflight(monkeypatch, service, "standard_workbook")
    def fake_pipeline(_source_path, output_path):
        output_path.write_bytes(b"result")
        return ExcelFinalProcessResult(
            protocol_version=1,
            output_path=output_path.resolve(),
            quality_status="ok",
            warning_count=0,
            severe_warning_count=0,
            report_summary={
                "info_count": 0,
                "warning_count": 0,
                "severe_warning_count": 0,
                "category_counts": {},
                "representative_messages": [],
            },
        )

    monkeypatch.setattr(service, "run_excel_final_pipeline", fake_pipeline)
    monkeypatch.setattr(
        service,
        "import_workbook_for_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("mysql://user:password@secret-db.internal/db")
        ),
    )

    run_excel_final_processing(job.id, expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    assert persisted.error_message == "MySQL 入库失败；请检查服务状态和处理报告"
    assert "secret-db.internal" not in caplog.text


def test_successful_warning_job_persists_and_broadcasts_quality(
    db: Session,
    monkeypatch,
    tmp_path,
):
    from app.modules.excel_processing import execution as service

    source = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/warning.xlsx",
        original_name="warning.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=10,
        sha256="f" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        priority=0,
        progress=0,
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.commit()

    source_path = tmp_path / "warning.xlsx"
    source_path.write_bytes(b"source")

    def fake_stage(worker_db: Session, file_id: int, _work_dir):
        return source_path, worker_db.get(StoredFile, file_id)

    def fake_pipeline(_source_path, output_path):
        workbook = Workbook()
        organized = workbook.active
        organized.title = "整理表"
        organized.append(
            [
                "序号",
                "构件编号",
                "类型",
                "零件号",
                "规格",
                "长度",
                "材质",
                "数量",
                "表净重",
                "表毛重",
                "重量核验",
            ]
        )
        organized.append([1, "C-1", "板材", "P-1", 10, 1000, "Q355B", 1, 7.0, 7.85, "警告"])
        report = workbook.create_sheet("处理报告")
        report.append(["级别", "类别", "说明"])
        report.append(["警告", "手册查无", "规格 X10 在指定类别中查无"])
        internal_output_path = output_path.with_name(".warning.internal.xlsx")
        workbook.save(internal_output_path)
        organized.delete_cols(11)
        workbook.save(output_path)
        return ExcelFinalProcessResult(
            protocol_version=1,
            output_path=output_path.resolve(),
            quality_status="warning",
            warning_count=1,
            severe_warning_count=0,
            report_summary={
                "info_count": 0,
                "warning_count": 1,
                "severe_warning_count": 0,
                "category_counts": {"手册查无": 1},
                "representative_messages": ["规格 X10 在指定类别中查无"],
            },
            internal_output_path=internal_output_path,
        )

    def fake_save(worker_db: Session, **kwargs):
        stored = StoredFile(
            bucket=kwargs["bucket"],
            storage_key=kwargs["storage_key"],
            original_name=kwargs["original_name"],
            file_ext=kwargs["file_ext"],
            content_type=kwargs["content_type"],
            size_bytes=len(kwargs["payload"]),
            sha256="e" * 64,
            uploaded_by=kwargs["uploaded_by"],
            status="available",
        )
        worker_db.add(stored)
        worker_db.flush()
        return stored

    monkeypatch.setattr(service, "stage_excel_source", fake_stage)
    _allow_worker_preflight(monkeypatch, service, "standard_workbook")
    monkeypatch.setattr(service, "run_excel_final_pipeline", fake_pipeline)
    monkeypatch.setattr(service, "save_bytes_as_file", fake_save)

    run_excel_final_processing(job.id, worker_name="quality-worker", expected_attempt=1)

    db.expire_all()
    persisted_job = db.get(Job, job.id)
    batch = db.scalar(select(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job.id))
    analysis = db.scalar(
        select(service.AnalysisResult).where(service.AnalysisResult.job_id == job.id)
    )
    steps = list(
        db.scalars(
            select(service.JobStep)
            .where(service.JobStep.job_id == job.id)
            .order_by(service.JobStep.id)
        )
    )

    assert persisted_job is not None
    assert persisted_job.status == "succeeded"
    assert batch is not None
    assert batch.quality_status == "warning"
    assert batch.warning_count == 1
    assert batch.severe_warning_count == 0
    imported_part = db.scalar(
        select(ExcelFinalPart).where(ExcelFinalPart.batch_id == batch.id)
    )
    assert imported_part is not None
    assert imported_part.weight_validation == "warning"
    assert analysis is not None
    assert analysis.result_json["quality_status"] == "warning"
    assert analysis.result_json["report_summary"]["category_counts"] == {"手册查无": 1}
    run_step = next(step for step in steps if step.step_name == "run_excel_final_pipeline")
    assert run_step.output_json["quality_status"] == "warning"
    assert run_step.output_json["output_name"] == "warning_处理后.xlsx"
    assert str(tmp_path) not in repr(
        [(step.input_json, step.output_json) for step in steps]
    )
    assert persisted_job.progress_data["quality_status"] == "warning"
    assert persisted_job.progress_data["warning_count"] == 1
    assert persisted_job.progress_data["severe_warning_count"] == 0
    assert persisted_job.progress_data["report_summary"]["category_counts"] == {"手册查无": 1}
    assert "手册查无=1" in persisted_job.progress_data["message"]
    status_payload = process_status(
        persisted_job,
        batch=batch,
        result_file_id=analysis.result_file_id,
    )
    assert status_payload["batch"]["quality_status"] == "warning"


def test_unknown_exception_message_never_echoes_internal_details():
    message = _exception_message(RuntimeError("mysql://user:password@secret-host/db"))

    assert message == "RuntimeError"
