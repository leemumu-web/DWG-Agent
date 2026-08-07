from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bh_reader.batch import BhBatchItem, BhBatchOutcome
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    DxfBhStage2ClassificationBatch,
    DxfBhStage2Input,
)
from app.modules.excel_processing.models import ExcelFinalBatch
from app.modules.excel_processing.stage2_execution import (
    BhReaderArtifacts,
    ExcelStage2WorkerInputs,
    Stage2WorkerError,
    run_bh_reader_batch,
    run_excel_stage2_processing,
    stage_registered_file,
)
from app.modules.excel_processing.stage_adapter import ExcelStage2ProcessResult
from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job, JobStep
from app.modules.projects.interface import Project
from app.platform.config.constants import STEP_RUN_EXCEL_STAGE2, TASK_EXCEL_STAGE2


def _stored(db: Session, name: str, *, digest: str) -> StoredFile:
    stored = StoredFile(
        bucket="test-stage2",
        storage_key=f"stage2/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=f".{name.rsplit('.', 1)[-1].lower()}",
        content_type="application/octet-stream",
        size_bytes=128,
        sha256=digest,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def _queued_job(db: Session) -> tuple[Job, ExcelStage2WorkerInputs]:
    user = User(
        username=f"stage2-worker-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Stage2 Worker Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"S2-{uuid4().hex[:8]}",
        name="Stage2 Worker",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    source = _stored(db, "source.xlsx", digest="a" * 64)
    stage1 = _stored(db, "stage1.xlsx", digest="b" * 64)
    drawing = _stored(db, "BH-1.dxf", digest="c" * 64)
    source.uploaded_by = user.id
    stage1.uploaded_by = user.id
    drawing.uploaded_by = user.id
    item = DxfBhStage2Input(
        classification_item_id=7,
        drawing_id=None,
        source_file_id=drawing.id,
        input_file_id=drawing.id,
        input_sha256=drawing.sha256,
        input_bucket=drawing.bucket,
        input_storage_key=drawing.storage_key,
        input_size_bytes=drawing.size_bytes,
        input_name=drawing.original_name,
        profile_normalized="BH500*300*12*20",
        type_source="catalog",
    )
    batch = DxfBhStage2ClassificationBatch(
        workflow_run_id=11,
        project_id=project.id,
        classification_run_id=13,
        classification_job_id=17,
        classification_job_attempt=1,
        classifier_version="1.2.0",
        input_manifest_sha256="d" * 64,
        bh_manifest_version=1,
        bh_manifest_sha256="e" * 64,
        items=(item,),
    )
    params = {
        "workflow_id": 11,
        "project_id": project.id,
        "source_excel_file_id": source.id,
        "source_excel_sha256": source.sha256,
        "stage1_artifact_id": 19,
        "stage1_result_id": 23,
        "stage1_excel_file_id": stage1.id,
        "stage1_excel_sha256": stage1.sha256,
        "stage1_job_id": 29,
        "stage1_job_attempt": 1,
        "classification_run_id": batch.classification_run_id,
        "classification_job_id": batch.classification_job_id,
        "classification_job_attempt": batch.classification_job_attempt,
        "classification_manifest_sha256": batch.input_manifest_sha256,
        "classifier_version": batch.classifier_version,
        "bh_input_count": len(batch.items),
        "bh_manifest_version": batch.bh_manifest_version,
        "bh_manifest_sha256": batch.bh_manifest_sha256,
    }
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type=TASK_EXCEL_STAGE2,
        pipeline="excel_stage2",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json=params,
    )
    db.add(job)
    db.commit()
    return job, ExcelStage2WorkerInputs(
        source_excel=source,
        stage1_excel=stage1,
        classification_batch=batch,
    )


@pytest.mark.parametrize(
    ("stage2_status", "missing", "manual"),
    [("complete", 0, 0), ("partial", 1, 0), ("noop", 0, 0)],
)
def test_stage2_worker_publishes_two_attempt_bound_results_and_mysql_projection(
    db: Session,
    monkeypatch,
    tmp_path: Path,
    stage2_status: str,
    missing: int,
    manual: int,
) -> None:
    from app.modules.excel_processing import stage2_execution as service
    from app.platform.config.settings import settings

    job, inputs = _queued_job(db)
    monkeypatch.setattr(settings, "excel_stage2_work_root", tmp_path / "work")
    monkeypatch.setattr(service, "resolve_excel_stage2_worker_inputs", lambda *_: inputs)

    def fake_stage(stored, destination, expected_sha256):
        assert stored.sha256 == expected_sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(stored.original_name.encode())
        return destination

    def fake_reader(_db, _job, _inputs, work_dir, _progress):
        reader = work_dir / "BH左右进读取表.xlsx"
        measurements = work_dir / "bh-measurements.json"
        reader.write_bytes(b"reader-xlsx")
        measurements.write_text(
            json.dumps({"schema": "bh_setback_measurements/v1", "items": []}),
            encoding="utf-8",
        )
        return BhReaderArtifacts(
            workbook_path=reader,
            measurements_path=measurements,
            processed_count=len(inputs.classification_batch.items),
            ok_count=len(inputs.classification_batch.items),
            failure_count=0,
        )

    rebuild_activity: list[dict[str, object]] = []

    def fake_pipeline(_stage1, _measurements, output, *, box_measurements_path=None, on_heartbeat=None):
        assert box_measurements_path is None  # 该用例无 BOX 图纸
        assert on_heartbeat is not None
        on_heartbeat()
        with service.SessionLocal() as observer:
            active = observer.get(Job, job.id)
            assert active is not None
            rebuild_activity.append(dict(active.progress_data))
        output.write_bytes(b"stage2-xlsx")
        internal = output.with_name("stage2.internal.xlsx")
        internal.write_bytes(b"stage2-internal")
        return ExcelStage2ProcessResult(
            protocol_version=1,
            output_path=output,
            internal_output_path=internal,
            status=stage2_status,
            matched_occurrence_count=0 if stage2_status == "noop" else 1,
            missing_drawing_count=missing,
            unmatched_drawing_count=0,
            manual_occurrence_count=manual,
            quality_status="warning" if stage2_status == "partial" else "ok",
            warning_count=1 if stage2_status == "partial" else 0,
            severe_warning_count=0,
            report_summary={
                "info_count": 0,
                "warning_count": 1 if stage2_status == "partial" else 0,
                "severe_warning_count": 0,
                "category_counts": {},
                "representative_messages": [],
            },
        )

    def fake_import(worker_db, **kwargs):
        batch = ExcelFinalBatch(
            job_id=kwargs["job_id"],
            file_id=kwargs["file_id"],
            source_type=kwargs["source_type"],
            source_name=kwargs["source_name"],
            part_count=2,
            component_count=1,
            quality_status=kwargs["expected_quality"]["quality_status"],
            warning_count=kwargs["expected_quality"]["warning_count"],
            severe_warning_count=kwargs["expected_quality"]["severe_warning_count"],
        )
        worker_db.add(batch)
        worker_db.flush()
        return batch, {
            "batch_id": batch.id,
            "parts_imported": 2,
            "components_imported": 1,
            "quality_status": batch.quality_status,
            "warning_count": batch.warning_count,
            "severe_warning_count": batch.severe_warning_count,
            "report_summary": {},
            "total_net_weight": None,
            "total_gross_weight": None,
        }

    saved_names: list[str] = []

    def fake_save(worker_db, **kwargs):
        saved_names.append(kwargs["original_name"])
        stored = _stored(worker_db, kwargs["original_name"], digest="f" * 64)
        stored.size_bytes = len(kwargs["payload"])
        stored.uploaded_by = kwargs["uploaded_by"]
        return stored

    monkeypatch.setattr(service, "stage_registered_file", fake_stage)
    monkeypatch.setattr(service, "run_bh_reader_batch", fake_reader)
    monkeypatch.setattr(service, "run_excel_stage2_pipeline", fake_pipeline)
    monkeypatch.setattr(service, "import_workbook_for_job", fake_import)
    monkeypatch.setattr(service, "prepare_generated_file_transfer", lambda *_a, **_k: "tx")
    monkeypatch.setattr(service, "complete_transfer_in_transaction", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "save_bytes_as_file", fake_save)

    run_excel_stage2_processing(job.id, worker_name="stage2-test", expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    results = list(
        db.scalars(
            select(AnalysisResult)
            .where(AnalysisResult.job_id == job.id)
            .order_by(AnalysisResult.id)
        )
    )
    batch = db.scalar(select(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job.id))
    assert persisted.status == "succeeded"
    assert persisted.progress == 100
    assert rebuild_activity == [
        {
            "type": "progress",
            "status": "running",
            "progress": 80,
            "step_name": STEP_RUN_EXCEL_STAGE2,
            "message": "正在深化整理表和 part 表",
            "phase": "rebuild_excel",
            "activity": "running",
            "job_id": job.id,
            "attempt": 1,
        }
    ]
    assert persisted.progress_data["stage2_status"] == stage2_status
    assert [result.result_json["workflow_artifact_type"] for result in results] == [
        "bh_setback_excel",
        "stage2_excel",
    ]
    assert all(result.result_json["job_attempt"] == 1 for result in results)
    assert batch is not None
    assert batch.source_type == "stage2_bh"
    assert batch.file_id == inputs.stage1_excel.id
    assert saved_names == ["BH左右进读取表.xlsx", "stage1_BH和BOX左右进处理后.xlsx"]
    assert not (tmp_path / "work" / "11" / str(job.id) / "attempt-1").exists()


def test_stage2_worker_fails_before_reader_when_frozen_manifest_changes(
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.modules.excel_processing import stage2_execution as service
    from app.platform.config.settings import settings

    job, _inputs = _queued_job(db)
    monkeypatch.setattr(settings, "excel_stage2_work_root", tmp_path / "work")
    monkeypatch.setattr(
        service,
        "resolve_excel_stage2_worker_inputs",
        lambda *_: (_ for _ in ()).throw(
            Stage2WorkerError(
                "EXCEL_STAGE2_INPUT_MANIFEST_CHANGED",
                "BH 图纸清单在任务启动后发生变化，请重新运行。",
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "run_bh_reader_batch",
        lambda *_a, **_k: pytest.fail("manifest drift must stop before Reader"),
    )

    run_excel_stage2_processing(job.id, expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    assert persisted.status == "failed"
    assert persisted.error_code == "EXCEL_STAGE2_INPUT_MANIFEST_CHANGED"
    assert "发生变化" in persisted.error_message
    assert not list(db.scalars(select(AnalysisResult).where(AnalysisResult.job_id == job.id)))


def test_stage2_stale_attempt_does_not_touch_inputs_or_work_directory(
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.modules.excel_processing import stage2_execution as service
    from app.platform.config.settings import settings

    job, _inputs = _queued_job(db)
    job.attempt = 2
    db.commit()
    monkeypatch.setattr(settings, "excel_stage2_work_root", tmp_path / "work")
    monkeypatch.setattr(
        service,
        "resolve_excel_stage2_worker_inputs",
        lambda *_: pytest.fail("stale task must stop at claim"),
    )

    run_excel_stage2_processing(job.id, expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    assert persisted.status == "queued"
    assert persisted.attempt == 2
    assert not (tmp_path / "work").exists()
    assert not list(db.scalars(select(JobStep).where(JobStep.job_id == job.id)))


def test_stage2_staging_rejects_storage_bytes_that_do_not_match_registry(
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.modules.excel_processing import stage2_execution as service

    payload = b"actual-storage-bytes"
    source_path = tmp_path / "object.xlsx"
    source_path.write_bytes(payload)
    stored = _stored(db, "source.xlsx", digest="0" * 64)
    stored.size_bytes = len(payload)
    db.commit()
    storage = SimpleNamespace(local_path=lambda *_: source_path)
    monkeypatch.setattr(service, "get_storage_backend", lambda: storage)
    destination = tmp_path / "work" / "source.xlsx"

    with pytest.raises(Stage2WorkerError) as raised:
        stage_registered_file(stored, destination, stored.sha256)

    assert raised.value.code == "EXCEL_STAGE2_INPUT_OBJECT_CHANGED"
    assert not destination.exists()
    assert not destination.with_suffix(".xlsx.part").exists()


def _many_bh_inputs(count: int) -> tuple[DxfBhStage2Input, ...]:
    return tuple(
        DxfBhStage2Input(
            classification_item_id=index,
            drawing_id=None,
            source_file_id=index,
            input_file_id=index,
            input_sha256=f"{index:064x}"[-64:],
            input_bucket="classified",
            input_storage_key=f"workflow/items/{index}.dxf",
            input_size_bytes=32,
            input_name=f"BH-{index}.dxf",
            profile_normalized="BH500*300*12*20",
            type_source="catalog",
        )
        for index in range(1, count + 1)
    )


def _reader_inputs(
    db: Session,
    items: tuple[DxfBhStage2Input, ...],
) -> ExcelStage2WorkerInputs:
    source = _stored(db, "source.xlsx", digest="a" * 64)
    stage1 = _stored(db, "stage1.xlsx", digest="b" * 64)
    return ExcelStage2WorkerInputs(
        source_excel=source,
        stage1_excel=stage1,
        classification_batch=DxfBhStage2ClassificationBatch(
            workflow_run_id=1,
            project_id=1,
            classification_run_id=1,
            classification_job_id=1,
            classification_job_attempt=1,
            classifier_version="1.2.0",
            input_manifest_sha256="c" * 64,
            bh_manifest_version=1,
            bh_manifest_sha256="d" * 64,
            items=items,
        ),
    )


def _fake_reader_files(work_dir: Path, count: int) -> BhReaderArtifacts:
    workbook = work_dir / "BH左右进读取表.xlsx"
    measurements = work_dir / "bh-measurements.json"
    workbook.write_bytes(b"reader-xlsx")
    measurements.write_text(
        json.dumps({"schema": "bh_setback_measurements/v1", "items": []}),
        encoding="utf-8",
    )
    return BhReaderArtifacts(
        workbook_path=workbook,
        measurements_path=measurements,
        processed_count=count,
        ok_count=count,
        failure_count=0,
    )


def _fake_stage2_result(output: Path) -> ExcelStage2ProcessResult:
    output.write_bytes(b"stage2-xlsx")
    internal = output.with_name("stage2.internal.xlsx")
    internal.write_bytes(b"stage2-internal")
    return ExcelStage2ProcessResult(
        protocol_version=1,
        output_path=output,
        internal_output_path=internal,
        status="complete",
        matched_occurrence_count=1,
        missing_drawing_count=0,
        unmatched_drawing_count=0,
        manual_occurrence_count=0,
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


def _fake_stage2_import(worker_db: Session, **kwargs):
    batch = ExcelFinalBatch(
        job_id=kwargs["job_id"],
        file_id=kwargs["file_id"],
        source_type=kwargs["source_type"],
        source_name=kwargs["source_name"],
        part_count=2,
        component_count=1,
        quality_status="ok",
        warning_count=0,
        severe_warning_count=0,
    )
    worker_db.add(batch)
    worker_db.flush()
    return batch, {
        "batch_id": batch.id,
        "parts_imported": 2,
        "components_imported": 1,
        "quality_status": "ok",
        "warning_count": 0,
        "severe_warning_count": 0,
        "report_summary": {},
        "total_net_weight": None,
        "total_gross_weight": None,
    }


def _patch_stage2_worker_storage(monkeypatch, service, *, fail_on_save: int | None = None):
    saved = 0

    def fake_stage(stored, destination, expected_sha256):
        assert stored.sha256 == expected_sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(stored.original_name.encode())
        return destination

    def fake_save(worker_db, **kwargs):
        nonlocal saved
        saved += 1
        if saved == fail_on_save:
            raise RuntimeError("simulated object storage failure")
        stored = _stored(worker_db, kwargs["original_name"], digest=f"{saved}" * 64)
        stored.size_bytes = len(kwargs["payload"])
        stored.uploaded_by = kwargs["uploaded_by"]
        return stored

    monkeypatch.setattr(service, "stage_registered_file", fake_stage)
    monkeypatch.setattr(service, "prepare_generated_file_transfer", lambda *_a, **_k: "tx")
    monkeypatch.setattr(service, "complete_transfer_in_transaction", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "save_bytes_as_file", fake_save)
    return lambda: saved


def test_bh_reader_streams_5000_inputs_without_db_queries_or_disk_accumulation(
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.modules.excel_processing import stage2_execution as service

    items = _many_bh_inputs(5000)
    inputs = _reader_inputs(db, items)
    input_dir = tmp_path / "bh-input"
    maximum_dxf_count = 0
    staged = 0

    def fake_stage(*, destination, **_kwargs):
        nonlocal maximum_dxf_count, staged
        assert not list(input_dir.glob("*.dxf"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"DXF")
        staged += 1
        maximum_dxf_count = max(maximum_dxf_count, len(list(input_dir.glob("*.dxf"))))
        return destination

    def fake_analyze(entries, **_kwargs):
        entry = tuple(entries)[0]
        return BhBatchOutcome((BhBatchItem(
            file_name=entry.file_name,
            part_number=Path(entry.file_name).stem,
            specification="BH500*300*12*20",
            status="OK",
            confidence=1.0,
            measurements=(),
            warnings=(),
            diagnostic_row=(),
        ),))

    def fake_write(path, _results, _diagnostics):
        path.write_bytes(b"reader")

    monkeypatch.setattr(service, "_stage_storage_object", fake_stage)
    monkeypatch.setattr(service, "analyze_manifest", fake_analyze)
    monkeypatch.setattr(service, "write_results_xlsx", fake_write)

    artifacts = run_bh_reader_batch(
        SimpleNamespace(get=lambda *_: pytest.fail("Reader must not query DB per file")),
        SimpleNamespace(id=1),
        inputs,
        tmp_path,
        lambda *_: None,
    )

    assert staged == 5000
    assert artifacts.processed_count == 5000
    assert maximum_dxf_count == 1
    assert not list(input_dir.glob("*.dxf"))


def test_bh_reader_rejects_duplicate_resolved_part_numbers(
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.modules.excel_processing import stage2_execution as service

    inputs = _reader_inputs(db, _many_bh_inputs(2))

    def fake_stage(*, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"DXF")
        return destination

    def fake_analyze(entries, **_kwargs):
        entry = tuple(entries)[0]
        return BhBatchOutcome((BhBatchItem(
            file_name=entry.file_name,
            part_number="P-001",
            specification="BH500*300*12*20",
            status="OK",
            confidence=1.0,
            measurements=(),
            warnings=(),
            diagnostic_row=(),
        ),))

    monkeypatch.setattr(service, "_stage_storage_object", fake_stage)
    monkeypatch.setattr(service, "analyze_manifest", fake_analyze)

    with pytest.raises(Stage2WorkerError) as raised:
        run_bh_reader_batch(
            db,
            SimpleNamespace(id=1),
            inputs,
            tmp_path,
            lambda *_: None,
        )

    assert raised.value.code == "EXCEL_STAGE2_DUPLICATE_PART_NUMBER"
    assert "P-001" in str(raised.value)
    assert raised.value.diagnostic.workbook_path.is_file()
    assert raised.value.diagnostic.measurements_path.is_file()
    assert raised.value.diagnostic.processed_count == 2
    assert not list((tmp_path / "bh-input").glob("*.dxf"))


def test_stage2_batch_blocker_persists_diagnostic_only_and_fails_job(
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.modules.excel_processing import stage2_execution as service
    from app.platform.config.settings import settings

    job, inputs = _queued_job(db)
    monkeypatch.setattr(settings, "excel_stage2_work_root", tmp_path / "work")
    monkeypatch.setattr(service, "resolve_excel_stage2_worker_inputs", lambda *_: inputs)
    _patch_stage2_worker_storage(monkeypatch, service)

    def blocked_reader(_db, _job, _inputs, work_dir, _progress):
        diagnostic = _fake_reader_files(work_dir, 1)
        raise service.Stage2ReaderBlockingError(
            "EXCEL_STAGE2_DUPLICATE_PART_NUMBER",
            "BH 零件号 P-001 重复。",
            diagnostic=diagnostic,
        )

    monkeypatch.setattr(service, "run_bh_reader_batch", blocked_reader)
    monkeypatch.setattr(
        service,
        "run_excel_stage2_pipeline",
        lambda *_: pytest.fail("blocked Reader must not run Excel Stage2"),
    )

    run_excel_stage2_processing(job.id, expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    results = list(db.scalars(select(AnalysisResult).where(AnalysisResult.job_id == job.id)))
    assert persisted.status == "failed"
    assert persisted.error_code == "EXCEL_STAGE2_DUPLICATE_PART_NUMBER"
    assert len(results) == 1
    assert results[0].result_json["workflow_artifact_type"] == "bh_setback_excel"
    assert results[0].result_json["diagnostic_only"] is True
    assert results[0].result_json["stage2_status"] == "failed"
    assert db.scalar(select(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job.id)) is None


@pytest.mark.parametrize(
    ("failure_phase", "expected_code"),
    [
        ("pipeline", "EXCEL_STAGE2_BASELINE_INVALID"),
        ("database", "EXCEL_STAGE2_DB_IMPORT_FAILED"),
        ("second_save", "EXCEL_STAGE2_INTERNAL_ERROR"),
    ],
)
def test_stage2_failure_after_reader_keeps_only_diagnostic_and_cleans_projection(
    db: Session,
    monkeypatch,
    tmp_path: Path,
    failure_phase: str,
    expected_code: str,
) -> None:
    from app.modules.excel_processing import stage2_execution as service
    from app.platform.config.settings import settings

    job, inputs = _queued_job(db)
    monkeypatch.setattr(settings, "excel_stage2_work_root", tmp_path / "work")
    monkeypatch.setattr(service, "resolve_excel_stage2_worker_inputs", lambda *_: inputs)
    saves = _patch_stage2_worker_storage(
        monkeypatch,
        service,
        fail_on_save=2 if failure_phase == "second_save" else None,
    )
    monkeypatch.setattr(
        service,
        "run_bh_reader_batch",
        lambda _db, _job, _inputs, work_dir, _progress: _fake_reader_files(work_dir, 1),
    )

    if failure_phase == "pipeline":
        monkeypatch.setattr(
            service,
            "run_excel_stage2_pipeline",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                Stage2WorkerError(
                    "EXCEL_STAGE2_BASELINE_INVALID",
                    "Excel 第一阶段基线核验失败。",
                )
            ),
        )
    else:
        monkeypatch.setattr(
            service,
            "run_excel_stage2_pipeline",
            lambda _stage1, _measurements, output, **_kwargs: _fake_stage2_result(output),
        )

    if failure_phase == "database":
        def fail_import(worker_db, **kwargs):
            _fake_stage2_import(worker_db, **kwargs)
            raise Stage2WorkerError(
                "EXCEL_STAGE2_DB_IMPORT_FAILED",
                "Excel 第二阶段数据库投影失败。",
            )

        monkeypatch.setattr(service, "import_workbook_for_job", fail_import)
    else:
        monkeypatch.setattr(service, "import_workbook_for_job", _fake_stage2_import)

    run_excel_stage2_processing(job.id, expected_attempt=1)

    db.expire_all()
    persisted = db.get(Job, job.id)
    results = list(db.scalars(select(AnalysisResult).where(AnalysisResult.job_id == job.id)))
    assert persisted.status == "failed"
    assert persisted.error_code == expected_code
    assert saves() == (2 if failure_phase == "second_save" else 1)
    assert len(results) == 1
    assert results[0].result_json["workflow_artifact_type"] == "bh_setback_excel"
    assert results[0].result_json["diagnostic_only"] is True
    assert results[0].result_json["stage2_status"] == "failed"
    assert results[0].result_json["error_code"] == expected_code
    assert db.scalar(select(ExcelFinalBatch).where(ExcelFinalBatch.job_id == job.id)) is None
    assert not (tmp_path / "work" / "11" / str(job.id) / "attempt-1").exists()


def test_job_dispatch_routes_excel_stage2_to_its_owned_enqueue_boundary(monkeypatch) -> None:
    from app.modules.jobs import dispatch
    from app.platform.config.constants import PIPELINE_EXCEL_STAGE2

    captured: list[tuple[int, int]] = []
    monkeypatch.setattr(
        dispatch,
        "enqueue_excel_stage2_job",
        lambda job_id, attempt: captured.append((job_id, attempt)) or "stage2-task",
        raising=False,
    )
    monkeypatch.setattr(
        dispatch,
        "enqueue_stub_job",
        lambda *_: pytest.fail("Excel Stage2 must not fall through to stub"),
    )

    assert dispatch.enqueue_job(41, PIPELINE_EXCEL_STAGE2, 3) == "stage2-task"
    assert captured == [(41, 3)]
