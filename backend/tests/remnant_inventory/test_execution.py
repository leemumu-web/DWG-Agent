from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest
from sqlalchemy import select

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.modules.remnant_inventory.models import RemnantImportBatch, RemnantImportItem


def _item(db, *, status: str = "parsing", attempt: int = 1) -> RemnantImportItem:
    user = User(username=f"exec-{status}-{attempt}", real_name="Exec", password_hash="x")
    db.add(user)
    db.flush()
    source = StoredFile(
        bucket="dxf-original",
        storage_key=f"tests/{status}-{attempt}.dxf",
        original_name="source.dxf",
        file_ext=".dxf",
        size_bytes=100,
        sha256=f"{attempt:064x}",
        status="available",
    )
    db.add(source)
    db.flush()
    batch = RemnantImportBatch(created_by=user.id, total_count=1, status="processing")
    db.add(batch)
    db.flush()
    item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=source.id,
        dxf_file_id=source.id,
        source_sha256=source.sha256,
        source_ext=".dxf",
        status=status,
        attempt=attempt,
    )
    db.add(item)
    db.flush()
    return item


def _stage_result():
    return SimpleNamespace(
        parser_version="0.1.0",
        schema_version="1.0",
        material_candidates=[SimpleNamespace(value="Q235B", evidence=[])],
        project_candidates=[SimpleNamespace(value="P-1", evidence=[])],
        part_candidates=[SimpleNamespace(value="L-1", evidence=[])],
        warnings=[],
        to_dict=lambda: {
            "material_candidates": [{"value": "Q235B", "evidence": []}],
            "project_candidates": [{"value": "P-1", "evidence": []}],
            "part_candidates": [{"value": "L-1", "evidence": []}],
            "warnings": [],
        },
    )


def test_old_parse_attempt_cannot_overwrite_retry(db) -> None:
    from app.modules.remnant_inventory.execution import store_parse_result

    item = _item(db, status="parsing", attempt=2)
    assert store_parse_result(db, item.id, expected_attempt=1, result=_stage_result()) is False
    db.refresh(item)
    assert item.status == "parsing"
    assert item.material_candidates_json is None


def test_current_parse_attempt_persists_candidates_and_pending_status(db) -> None:
    from app.modules.remnant_inventory.execution import store_parse_result

    item = _item(db)
    assert store_parse_result(db, item.id, expected_attempt=1, result=_stage_result()) is True
    db.refresh(item)
    assert item.status == "pending_confirmation"
    assert item.parser_version == "0.1.0"
    assert item.material_candidates_json == [{"value": "Q235B", "evidence": []}]


def test_parse_result_resolves_unique_material_code_or_alias(db) -> None:
    from app.modules.remnant_inventory.execution import store_parse_result
    from app.modules.remnant_inventory.models import RemnantMaterial, RemnantMaterialAlias

    item = _item(db)
    material = RemnantMaterial(code="Q235B", family_code="Q235", enabled=True)
    db.add(material)
    db.flush()
    db.add(
        RemnantMaterialAlias(
            material_id=material.id,
            alias="GB-Q235B",
            normalized_alias="GB-Q235B",
        )
    )
    db.flush()
    result = _stage_result()
    result.material_candidates = [SimpleNamespace(value="GB-Q235B", evidence=[])]
    result.to_dict = lambda: {
        "material_candidates": [{"value": "GB-Q235B", "evidence": []}],
        "project_candidates": [{"value": "P-1", "evidence": []}],
        "part_candidates": [{"value": "L-1", "evidence": []}],
        "warnings": [],
    }

    assert store_parse_result(db, item.id, expected_attempt=1, result=result) is True
    db.refresh(item)
    assert item.corrected_material_id == material.id


def test_auto_parse_uses_standard_offcut_and_batch_project(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.execution import store_parse_result
    from app.modules.remnant_inventory.models import RemnantMaterial

    item = _item(db)
    batch = db.get(RemnantImportBatch, item.batch_id)
    batch.import_mode = "auto"
    batch.default_project_no = "PROJECT-A"
    result = _stage_result()
    result.schema_version = "1.1"
    result.standard_offcut = SimpleNamespace(
        block_type="OffCut_Zh_Cn",
        raw_specification="-12.5 × 1000 X 2000",
        thickness=Decimal("12.5"),
        length=Decimal("1000"),
        width=Decimal("2000"),
        material=" q355b ",
        remnant_number="YL-001",
    )
    result.to_dict = lambda: {
        "material_candidates": [],
        "project_candidates": [],
        "part_candidates": [],
        "warnings": [],
        "standard_offcut": {
            "block_type": "OffCut_Zh_Cn",
            "raw_specification": "-12.5 × 1000 X 2000",
            "thickness": "12.5",
            "length": "1000",
            "width": "2000",
            "material": " q355b ",
            "remnant_number": "YL-001",
        },
    }

    assert store_parse_result(db, item.id, expected_attempt=1, result=result) is True
    db.refresh(item)
    material = db.get(RemnantMaterial, item.corrected_material_id)
    assert item.standard_parse_json == result.to_dict()["standard_offcut"]
    assert item.corrected_thickness_mm == Decimal("12.500")
    assert item.corrected_project_no == "PROJECT-A"
    assert item.corrected_parts_json == ["YL-001"]
    assert (material.code, material.family_code, material.enabled) == ("Q355B", "Q355B", True)
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "remnants.material.auto_create")
    )
    assert audit.actor_user_id == batch.created_by


def test_auto_parse_without_standard_summary_keeps_project_and_allows_manual_fill(db) -> None:
    from app.modules.remnant_inventory.execution import store_parse_result

    item = _item(db)
    batch = db.get(RemnantImportBatch, item.batch_id)
    batch.import_mode = "auto"
    batch.default_project_no = "PROJECT-B"
    result = _stage_result()
    result.schema_version = "1.0"
    result.standard_offcut = None

    assert store_parse_result(db, item.id, expected_attempt=1, result=result) is True
    db.refresh(item)
    assert item.status == "pending_confirmation"
    assert item.standard_parse_json is None
    assert item.corrected_project_no == "PROJECT-B"
    assert item.corrected_material_id is None
    assert item.corrected_parts_json is None


def test_auto_parse_reenables_disabled_standard_material_with_audit(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.execution import store_parse_result
    from app.modules.remnant_inventory.models import RemnantMaterial

    item = _item(db)
    batch = db.get(RemnantImportBatch, item.batch_id)
    batch.import_mode = "auto"
    batch.default_project_no = "PROJECT-C"
    material = RemnantMaterial(
        code="Q355B",
        family_code="Q355",
        enabled=False,
    )
    db.add(material)
    db.flush()
    result = _stage_result()
    result.schema_version = "1.1"
    result.to_dict = lambda: {
        "material_candidates": [],
        "project_candidates": [],
        "part_candidates": [],
        "warnings": [],
        "standard_offcut": {
            "block_type": "OffCut_Zh_Cn",
            "raw_specification": "12 × 1000 × 2000",
            "thickness": "12",
            "length": "1000",
            "width": "2000",
            "material": "Q355B",
            "remnant_number": "YL-002",
        },
    }

    assert store_parse_result(db, item.id, expected_attempt=1, result=result) is True
    db.refresh(material)
    db.refresh(item)
    assert material.enabled is True
    assert item.corrected_material_id == material.id
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "remnants.material.auto_enable")
    )
    assert audit.actor_user_id == batch.created_by


def test_stage_adapter_runs_isolated_cli_and_restores_typed_result(tmp_path: Path) -> None:
    from app.modules.remnant_inventory.stage_adapter import parse_staged_dxf

    staged = tmp_path / "source.dxf"
    document = ezdxf.new("R2018")
    document.modelspace().add_text("材质: Q235B-Z15")
    document.saveas(staged)

    result = parse_staged_dxf(staged)

    assert result.schema_version == "1.1"
    assert result.source_sha256
    assert any(candidate.value == "Q235B-Z15" for candidate in result.material_candidates)


def test_batch_counters_are_recalculated_from_item_truth(db) -> None:
    from app.modules.remnant_inventory.execution import recalculate_batch_counters

    item = _item(db, status="pending_confirmation")
    batch = db.get(RemnantImportBatch, item.batch_id)
    batch.total_count = 99
    db.add(
        RemnantImportItem(
            batch_id=batch.id,
            source_file_id=item.source_file_id,
            source_sha256="f" * 64,
            source_ext=".dxf",
            status="failed",
            attempt=1,
        )
    )
    db.flush()
    recalculate_batch_counters(db, batch.id)
    db.refresh(batch)
    assert (batch.total_count, batch.pending_count, batch.failed_count) == (2, 1, 1)


def test_failed_and_cancelled_terminal_rows_leave_batch_awaiting_attention(db) -> None:
    from app.modules.remnant_inventory.execution import recalculate_batch_counters

    item = _item(db, status="failed")
    batch = db.get(RemnantImportBatch, item.batch_id)
    db.add(
        RemnantImportItem(
            batch_id=batch.id,
            source_file_id=item.source_file_id,
            source_sha256="e" * 64,
            source_ext=".dxf",
            status="cancelled",
            attempt=1,
        )
    )
    db.flush()

    recalculate_batch_counters(db, batch.id)

    assert batch.status == "awaiting_confirmation"
    assert (batch.failed_count, batch.cancelled_count) == (1, 1)


def test_cad_directory_conversion_invokes_oda_once_and_maps_item_ids(
    tmp_path: Path, monkeypatch
) -> None:
    from app.modules.cad_processing.interface import convert_dwg_directory

    source_dir = tmp_path / "input"
    source_dir.mkdir()
    sources = {1: source_dir / "1.dwg", 2: source_dir / "2.dwg"}
    for source in sources.values():
        source.write_bytes(b"AC1032" + b"x" * 1024)
    calls: list[Path] = []

    def fake_convert_directory(source_dir, target_dir, **_kwargs):
        calls.append(source_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for source in sorted(source_dir.glob("*.dwg")):
            target = target_dir / f"{source.stem}.dxf"
            target.write_text("0\nEOF\n")
            results.append(SimpleNamespace(source=source, target=target, success=True))
        return SimpleNamespace(results=results)

    monkeypatch.setattr("dwg_converter.convert_directory", fake_convert_directory)
    outputs = convert_dwg_directory(sources, tmp_path / "output")
    assert calls == [source_dir]
    assert set(outputs) == {1, 2}


def test_remnant_tasks_and_queues_are_registered() -> None:
    from app.platform.messaging.celery_app import celery_app

    assert "app.workers.tasks_remnant_convert.convert_batch" in celery_app.tasks
    assert "app.workers.tasks_remnant_parse.parse_item" in celery_app.tasks
    routes = celery_app.conf.task_routes
    assert routes["app.workers.tasks_remnant_convert.*"]["queue"] == "remnant_convert"
    assert routes["app.workers.tasks_remnant_parse.*"]["queue"] == "remnant_parse"


def test_compose_has_dedicated_bounded_workers() -> None:
    from tests.support.paths import REPO_ROOT

    source = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "worker-remnant-convert:" in source
    assert "-Q remnant_convert" in source
    assert "REMNANT_CONVERT_WORKER_CONCURRENCY:-2" in source
    assert "worker-remnant-parse:" in source
    assert "-Q remnant_parse" in source
    assert "REMNANT_PARSE_WORKER_CONCURRENCY:-4" in source


def test_prepare_execution_creates_truthful_jobs_for_both_file_types(db) -> None:
    from app.modules.remnant_inventory.execution import prepare_import_execution

    user = User(username="prepare-worker", real_name="Prepare", password_hash="x")
    db.add(user)
    db.flush()
    dwg = StoredFile(
        bucket="dwg-original",
        storage_key="tests/prepare.dwg",
        original_name="a.dwg",
        file_ext=".dwg",
        size_bytes=1024,
        sha256="a" * 64,
        status="available",
    )
    dxf = StoredFile(
        bucket="dxf-original",
        storage_key="tests/prepare.dxf",
        original_name="b.dxf",
        file_ext=".dxf",
        size_bytes=100,
        sha256="b" * 64,
        status="available",
    )
    db.add_all([dwg, dxf])
    db.flush()
    batch = RemnantImportBatch(created_by=user.id, total_count=2)
    db.add(batch)
    db.flush()
    dwg_item = RemnantImportItem(
        batch_id=batch.id, source_file_id=dwg.id, source_sha256=dwg.sha256, source_ext=".dwg"
    )
    dxf_item = RemnantImportItem(
        batch_id=batch.id, source_file_id=dxf.id, source_sha256=dxf.sha256, source_ext=".dxf"
    )
    db.add_all([dwg_item, dxf_item])
    db.flush()

    dispatch = prepare_import_execution(db, batch.id, actor_id=user.id)
    db.flush()

    db.refresh(dwg_item)
    db.refresh(dxf_item)
    assert dispatch.convert_attempts == {dwg_item.id: 1}
    assert dispatch.parse_attempts == {dxf_item.id: 1}
    assert (dwg_item.status, dxf_item.status, dxf_item.dxf_file_id) == (
        "converting",
        "parsing",
        dxf.id,
    )
    jobs = {job.id: job for job in db.query(Job).all()}
    assert jobs[dwg_item.conversion_job_id].pipeline == "remnant_convert"
    assert jobs[dwg_item.parse_job_id].pipeline == "remnant_parse"
    assert jobs[dxf_item.parse_job_id].pipeline == "remnant_parse"


def test_run_parse_item_completes_job_and_keeps_item_attempt_fence(db, monkeypatch) -> None:
    from app.modules.remnant_inventory import execution
    from app.modules.remnant_inventory.execution import prepare_import_execution

    item = _item(db, status="uploaded", attempt=2)
    batch = db.get(RemnantImportBatch, item.batch_id)
    dispatch = prepare_import_execution(db, batch.id, actor_id=batch.created_by)
    db.commit()
    monkeypatch.setattr(
        execution, "_stage_file", lambda _source, target: target.write_text("0\nEOF\n")
    )
    monkeypatch.setattr(execution, "parse_staged_dxf", lambda _path: _stage_result())

    execution.run_parse_item(item.id, dispatch.parse_attempts[item.id])

    db.expire_all()
    current = db.get(RemnantImportItem, item.id)
    job = db.get(Job, current.parse_job_id)
    assert current.status == "pending_confirmation"
    assert current.attempt == 2
    assert job.status == "succeeded"


def test_converter_exception_terminalizes_item_and_both_jobs(db, monkeypatch) -> None:
    from app.modules.remnant_inventory import execution
    from app.modules.remnant_inventory.execution import prepare_import_execution

    user = User(username="convert-crash", real_name="Crash", password_hash="x")
    db.add(user)
    db.flush()
    source = StoredFile(
        bucket="dwg-original",
        storage_key="tests/convert-crash.dwg",
        original_name="convert-crash.dwg",
        file_ext=".dwg",
        size_bytes=1024,
        sha256="c" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    batch = RemnantImportBatch(created_by=user.id, total_count=1)
    db.add(batch)
    db.flush()
    item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=source.id,
        source_sha256=source.sha256,
        source_ext=".dwg",
    )
    db.add(item)
    db.flush()
    dispatch = prepare_import_execution(db, batch.id, actor_id=user.id)
    db.commit()
    def fake_stage(_source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"AC1032")

    monkeypatch.setattr(execution, "_stage_file", fake_stage)
    monkeypatch.setattr(
        execution, "convert_dwg_directory", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ODA crashed"))
    )

    execution.run_conversion_batch(batch.id, dispatch.convert_attempts)

    db.expire_all()
    current = db.get(RemnantImportItem, item.id)
    assert current.status == "failed"
    assert current.error_code == "REMNANT_CONVERSION_FAILED"
    assert db.get(Job, current.conversion_job_id).status == "failed"
    assert db.get(Job, current.parse_job_id).status == "failed"


def test_broker_dispatch_failure_terminalizes_direct_dxf_item(db, monkeypatch) -> None:
    from app.modules.remnant_inventory.execution import (
        dispatch_import_execution,
        prepare_import_execution,
    )

    item = _item(db, status="uploaded")
    batch = db.get(RemnantImportBatch, item.batch_id)
    dispatch = prepare_import_execution(db, batch.id, actor_id=batch.created_by)
    db.commit()

    def fail_delay(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.modules.remnant_inventory.tasks.parse_item_task.delay", fail_delay
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        dispatch_import_execution(dispatch)

    db.expire_all()
    current = db.get(RemnantImportItem, item.id)
    assert current.status == "failed"
    assert current.error_code == "REMNANT_DISPATCH_FAILED"
    assert db.get(Job, current.parse_job_id).status == "failed"


def test_conversion_dispatch_failure_terminalizes_undispatched_mixed_batch(
    db, monkeypatch
) -> None:
    from app.modules.remnant_inventory.execution import (
        dispatch_import_execution,
        prepare_import_execution,
    )

    dxf_item = _item(db, status="uploaded")
    batch = db.get(RemnantImportBatch, dxf_item.batch_id)
    dwg = StoredFile(
        bucket="dwg-original",
        storage_key="tests/mixed-dispatch.dwg",
        original_name="mixed-dispatch.dwg",
        file_ext=".dwg",
        size_bytes=1024,
        sha256="d" * 64,
        status="available",
    )
    db.add(dwg)
    db.flush()
    dwg_item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=dwg.id,
        source_sha256=dwg.sha256,
        source_ext=".dwg",
        status="uploaded",
    )
    db.add(dwg_item)
    db.flush()
    dispatch = prepare_import_execution(db, batch.id, actor_id=batch.created_by)
    db.commit()

    monkeypatch.setattr(
        "app.modules.remnant_inventory.tasks.convert_batch_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        dispatch_import_execution(dispatch)

    db.expire_all()
    for item_id in (dxf_item.id, dwg_item.id):
        current = db.get(RemnantImportItem, item_id)
        assert current.status == "failed"
        assert current.error_code == "REMNANT_DISPATCH_FAILED"
        assert db.get(Job, current.parse_job_id).status == "failed"
