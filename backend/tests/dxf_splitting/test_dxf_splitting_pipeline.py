from __future__ import annotations

import json
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import ezdxf
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.dxf_classification.interface import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.dxf_splitting import adapter as split_adapter
from app.modules.dxf_splitting import execution as split_execution
from app.modules.dxf_splitting import models as split_models
from app.modules.dxf_splitting.interface import (
    DxfSplitRun,
    get_excel_split_handoff,
    manual_review_archive_members,
)
from app.modules.files.interface import (
    StoredFile,
    clear_storage_backend_cache,
    get_storage_backend,
    save_bytes_as_file,
)
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job, JobStep
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows import interface as workflow_service
from app.modules.workflows.interface import WorkflowRun
from app.modules.workflows.routes.archive import _collect_archive_members
from app.modules.workflows.schemas import WorkflowCreate
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException


def test_review_decision_model_keeps_candidates_separate_from_formal_outputs():
    run_columns = set(split_models.DxfSplitRun.__table__.columns.keys())
    item_columns = set(split_models.DxfSplitItem.__table__.columns.keys())
    decision_model = getattr(split_models, "DxfSplitReviewDecision", None)

    assert "processed_count" in run_columns
    assert {
        "candidate_normal_dxf_file_id",
        "candidate_weld_allowance_dxf_file_id",
        "candidate_split_report_file_id",
        "candidate_weld_allowance_report_file_id",
    } <= item_columns
    assert decision_model is not None
    assert {
        "split_item_id",
        "decision",
        "final_normal_dxf_file_id",
        "final_weld_allowance_dxf_file_id",
        "comment",
        "decided_by",
        "decided_at",
        "version",
    } <= set(decision_model.__table__.columns.keys())


def _valid_dxf_bytes(tmp_path: Path, name: str) -> bytes:
    path = tmp_path / name
    document = ezdxf.new("R2010")
    document.modelspace().add_line((0, 0), (100, 0))
    document.saveas(path)
    return path.read_bytes()


def _configure_local_storage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_root", tmp_path / "storage")
    clear_storage_backend_cache()


def _save_source_dxf(
    db,
    *,
    user_id: int,
    payload: bytes,
    original_name: str,
) -> StoredFile:
    return save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_dxf_derived,
        storage_key=f"tests/dxf-split-input/{uuid4().hex}.dxf",
        original_name=original_name,
        file_ext=".dxf",
        content_type="application/dxf",
        payload=payload,
        uploaded_by=user_id,
        batch_name="classified-input",
    )


def _save_report(
    db,
    *,
    user_id: int,
    original_name: str,
    payload: bytes,
) -> StoredFile:
    extension = Path(original_name).suffix.casefold()
    return save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_reports,
        storage_key=f"tests/dxf-split-input/{uuid4().hex}{extension}",
        original_name=original_name,
        file_ext=extension,
        content_type="application/json",
        payload=payload,
        uploaded_by=user_id,
        batch_name="classification-ledger",
    )


def _split_job_fixture(
    db,
    tmp_path: Path,
    *,
    parts: tuple[tuple[str, str], ...],
) -> tuple[int, int, dict[str, int]]:
    user = User(
        username=f"split-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Split Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"SPLIT-{uuid4().hex[:6]}",
        name="DXF split",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectMember(
            project_id=project.id,
            user_id=user.id,
            project_role="project_owner",
        )
    )
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="Split batch",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    workflow_service.start_workflow(db, workflow)

    classification_job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="classify_steel_dxf",
        pipeline="steel_dxf_classifier",
        status="succeeded",
        attempt=1,
        progress=100,
        precision_level="normal",
        params_json={
            "workflow_id": workflow.id,
            "input_manifest_sha256": "a" * 64,
        },
    )
    db.add(classification_job)
    db.flush()
    report = _save_report(
        db,
        user_id=user.id,
        original_name="分类报告.json",
        payload=b'{"schema":"STEEL-DXF-CLASSIFICATION-1.2"}',
    )
    manifest = _save_report(
        db,
        user_id=user.id,
        original_name="分类清单.csv",
        payload=b"name,status\n",
    )
    run = DxfClassificationRun(
        workflow_run_id=workflow.id,
        project_id=project.id,
        job_id=classification_job.id,
        job_attempt=1,
        status="completed",
        classifier_version="1.2.0",
        report_schema="STEEL-DXF-CLASSIFICATION-1.2",
        cli_schema="STEEL-DXF-CLI-1.2",
        project_name=f"{project.code}-workflow-{workflow.id}",
        input_manifest_sha256="a" * 64,
        input_count=len(parts),
        classified_count=len(parts),
        review_required_count=0,
        unreadable_count=0,
        type_counts_json={
            part_type: sum(value == part_type for _, value in parts)
            for part_type in {value for _, value in parts}
        },
        report_file_id=report.id,
        manifest_file_id=manifest.id,
    )
    db.add(run)
    db.flush()
    source_file_ids: dict[str, int] = {}
    dxf_payload = _valid_dxf_bytes(tmp_path, f"source-{uuid4().hex}.dxf")
    for member, part_type in parts:
        source = _save_source_dxf(
            db,
            user_id=user.id,
            payload=dxf_payload,
            original_name=f"{member}_拆板前.dxf",
        )
        source_file_ids[member] = source.id
        db.add(
            DxfClassificationItem(
                run=run,
                source_file_id=source.id,
                output_file_id=source.id,
                source_name=source.original_name,
                output_name=source.original_name,
                output_directory=f"{run.project_name}_{part_type}_dxf",
                disposition="classified",
                part_type=part_type,
                profile_raw=f"{part_type}-PROFILE",
                profile_normalized=f"{part_type}-PROFILE",
                type_source="catalog",
                group_key=f"type:{part_type}",
                next_stage_eligible=True,
                diagnostics_json=[],
                evidence_json={},
            )
        )
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="dxf_classification",
            artifact_type="classified_dxf",
            file_id=source.id,
        )
    for artifact_type, stored in (
        ("classification_report", report),
        ("classification_manifest", manifest),
    ):
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="dxf_classification",
            artifact_type=artifact_type,
            file_id=stored.id,
        )
    db.flush()

    source_stage = next(item for item in workflow.stages if item.stage_code == "source_intake")
    classification_stage = next(
        item for item in workflow.stages if item.stage_code == "dxf_classification"
    )
    drawing_stage = next(
        item for item in workflow.stages if item.stage_code == "drawing_processing"
    )
    source_stage.status = "succeeded"
    source_stage.progress = 100
    classification_stage.status = "succeeded"
    classification_stage.progress = 100
    classification_stage.job_id = classification_job.id
    classification_stage.job_attempt = 1
    drawing_stage.status = "waiting_input"
    workflow.current_stage = "drawing_processing"
    workflow.status = "waiting_input"

    split_job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="split_steel_dxf",
        pipeline="steel_dxf_split",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        request_key=f"workflow-{workflow.id}-drawing_processing",
        params_json={
            "workflow_id": workflow.id,
            "classification_run_id": run.id,
            "input_manifest_sha256": run.input_manifest_sha256,
        },
    )
    db.add(split_job)
    db.flush()
    workflow_service.bind_stage_job(
        db,
        workflow,
        stage_code="drawing_processing",
        job=split_job,
    )
    db.commit()
    return workflow.id, split_job.id, source_file_ids


def _write_ledger(output_directory: Path) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "BH拆板信息"
    sheet.append(["零件号", "BH尺寸", "上下翼板是否相同"])
    path = output_directory / "BH拆板信息表.xlsx"
    workbook.save(path)
    workbook.close()
    return path


def _fake_splitter(
    *,
    family_by_member: dict[str, str],
    manual_members: frozenset[str] = frozenset(),
    invalid_members: frozenset[str] = frozenset(),
):
    def invoke(
        input_directory: Path,
        output_directory: Path,
        *,
        expected_input_count: int,
    ) -> dict[str, object]:
        results: list[dict[str, object]] = []
        for source in sorted(input_directory.glob("*.dxf")):
            member = source.stem.removesuffix("_拆板前")
            family = family_by_member[member]
            if member in manual_members:
                results.append(
                    {
                        "input": str(source.resolve()),
                        "compiler_version": "1.5.2",
                        "family": family,
                        "automation_route": "manual_review",
                        "native_automation_route": "review_required",
                        "disposition": "review_required",
                        "diagnostic_codes": ["FIXTURE_REVIEW"],
                    }
                )
                continue
            task_directory = output_directory / "auto_accepted" / family.casefold() / member
            task_directory.mkdir(parents=True)
            normal = task_directory / f"{member}_正常拆板.dxf"
            allowance = task_directory / f"{member}_余量增长.dxf"
            document = ezdxf.new("R2010")
            document.modelspace().add_line((0, 0), (100, 0))
            document.saveas(normal)
            document.saveas(allowance)
            split_report = task_directory / f"{member}_report.json"
            allowance_report = task_directory / f"{member}_weld_allowance_report.json"
            declared_normal = allowance if member in invalid_members else normal
            split_report.write_text(
                json.dumps(
                    {
                        "automation_route": "auto_accepted",
                        "paired_output": {
                            "status": "auto_accepted",
                            "normal_dxf": str(declared_normal.resolve()),
                            "weld_allowance_dxf": str(allowance.resolve()),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            allowance_report.write_text(
                json.dumps({"status": "completed"}, ensure_ascii=False),
                encoding="utf-8",
            )
            results.append(
                {
                    "input": str(source.resolve()),
                    "compiler_version": "1.5.2",
                    "family": family,
                    "production_clean": str(normal.resolve()),
                    "weld_allowance": str(allowance.resolve()),
                    "report": str(split_report.resolve()),
                    "weld_allowance_report": str(allowance_report.resolve()),
                    "task_dir": str(task_directory.resolve()),
                    "automation_route": "auto_accepted",
                    "native_automation_route": "production",
                    "disposition": "auto_accepted",
                    "diagnostic_codes": [],
                }
            )
        _write_ledger(output_directory)
        manual_count = sum(result["automation_route"] == "manual_review" for result in results)
        assert len(results) == expected_input_count
        return {
            "schema": split_adapter.CLI_SCHEMA,
            "splitter_version": split_adapter.SPLITTER_VERSION,
            "status": ("completed_with_review" if manual_count else "completed"),
            "exit_code": 1 if manual_count else 0,
            "input_count": len(results),
            "auto_accepted_count": len(results) - manual_count,
            "manual_review_count": manual_count,
            "source_contracts": {
                "BH": split_adapter.BH_SOURCE_CONTRACT,
                "BOX": split_adapter.BOX_SOURCE_CONTRACT,
            },
            "results": results,
        }

    return invoke


def test_adapter_uses_pinned_source_contracts_and_version(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    def run(command, **_kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=json.dumps(
                [
                    {
                        "input": str(tmp_path / "BH-1_拆板前.dxf"),
                        "compiler_version": "1.5.2",
                        "automation_route": "manual_review",
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(split_adapter.subprocess, "run", run)
    payload = split_adapter.invoke_splitter(
        tmp_path,
        tmp_path.parent / "output",
        expected_input_count=1,
    )

    assert payload["status"] == "completed_with_review"
    assert captured == [
        [
            split_adapter.sys.executable,
            "-m",
            "steel_dxf_split.cli",
            str(tmp_path),
            "--output-dir",
            str(tmp_path.parent / "output"),
            "--authorize-tekla-bh-single-part-profile",
            "project_tekla_bh_dxf_v1",
            "--authorize-tekla-box-single-part-profile",
            "project_tekla_box_dxf_v1",
        ]
    ]


def test_completed_batch_persists_exact_pairs_minio_ledger_and_excel_handoff(
    db,
    monkeypatch,
    tmp_path,
):
    _configure_local_storage(monkeypatch, tmp_path)
    workflow_id, job_id, _ = _split_job_fixture(
        db,
        tmp_path,
        parts=(("BH-1", "BH"), ("BOX-1", "BOX")),
    )
    monkeypatch.setattr(
        split_execution,
        "_invoke_splitter",
        _fake_splitter(
            family_by_member={"BH-1": "BH", "BOX-1": "BOX"},
        ),
    )

    split_execution.run_dxf_splitting(job_id, worker_name="test-split")

    db.expire_all()
    job = db.get(Job, job_id)
    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.job_id == job_id,
            DxfSplitRun.job_attempt == 1,
        )
    )
    assert job is not None and job.status == "succeeded"
    assert run is not None and run.status == "completed"
    steps = db.scalars(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.attempt == 1)
        .order_by(JobStep.id)
    ).all()
    assert [step.step_name for step in steps] == [
        "run_steel_dxf_split",
        "validate_steel_dxf_split",
        "persist_steel_dxf_split",
    ]
    assert run.input_count == 2
    assert run.auto_accepted_count == 2
    assert run.manual_review_count == 0
    assert run.source_contracts_json == {
        "BH": "project_tekla_bh_dxf_v1",
        "BOX": "project_tekla_box_dxf_v1",
    }
    assert {item.source_contract_id for item in run.items} == {
        "project_tekla_bh_dxf_v1",
        "project_tekla_box_dxf_v1",
    }
    normal_names = {db.get(StoredFile, item.normal_dxf_file_id).original_name for item in run.items}
    allowance_names = {
        db.get(StoredFile, item.weld_allowance_dxf_file_id).original_name for item in run.items
    }
    assert normal_names == {"BH-1_正常拆板.dxf", "BOX-1_正常拆板.dxf"}
    assert allowance_names == {"BH-1_余量增长.dxf", "BOX-1_余量增长.dxf"}
    for item in run.items:
        normal = db.get(StoredFile, item.normal_dxf_file_id)
        allowance = db.get(StoredFile, item.weld_allowance_dxf_file_id)
        assert normal.bucket == settings.minio_bucket_dxf_derived
        assert allowance.bucket == settings.minio_bucket_dxf_derived
        assert normal.storage_key.startswith(
            f"workflows/{workflow_id}/drawing-processing/attempt-1/"
        )
        assert allowance.storage_key.startswith(
            f"workflows/{workflow_id}/drawing-processing/attempt-1/"
        )
        assert get_storage_backend().object_exists(
            normal.bucket,
            normal.storage_key,
        )
        assert get_storage_backend().object_exists(
            allowance.bucket,
            allowance.storage_key,
        )
    ledger = db.get(StoredFile, run.bh_split_ledger_file_id)
    manifest = db.get(StoredFile, run.split_manifest_file_id)
    validation = db.get(StoredFile, run.validation_report_file_id)
    assert ledger.original_name == "BH拆板信息表.xlsx"
    assert ledger.bucket == settings.minio_bucket_reports
    assert manifest.bucket == settings.minio_bucket_reports
    assert validation.bucket == settings.minio_bucket_reports

    workflow = db.get(WorkflowRun, workflow_id)
    assert workflow is not None
    project_engineer = User(
        username=f"split-engineer-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Split Project Engineer",
        status="active",
    )
    db.add(project_engineer)
    db.flush()
    db.add(
        ProjectMember(
            project_id=workflow.project_id,
            user_id=project_engineer.id,
            project_role="project_engineer",
        )
    )
    db.flush()
    archive_members, drawing_stage = _collect_archive_members(
        db,
        project_engineer,
        workflow,
    )
    assert drawing_stage is None
    archived_file_ids = {file_id for file_id, _ in archive_members}
    formal_split_file_ids = {
        ledger.id,
        manifest.id,
        validation.id,
        *[
            file_id
            for item in run.items
            for file_id in (
                item.normal_dxf_file_id,
                item.weld_allowance_dxf_file_id,
            )
            if file_id is not None
        ],
    }
    assert formal_split_file_ids <= archived_file_ids
    job.status = "failed"
    db.flush()
    failed_archive_members, _ = _collect_archive_members(
        db,
        project_engineer,
        workflow,
    )
    failed_archive_file_ids = {file_id for file_id, _ in failed_archive_members}
    assert formal_split_file_ids.isdisjoint(failed_archive_file_ids)
    job.status = "succeeded"
    db.flush()

    workflow_service.sync_workflow_from_jobs(db, workflow)
    assert workflow.current_stage == "excel_stage1"
    assert workflow.status == "waiting_input"
    handoff = get_excel_split_handoff(db, workflow_id)
    assert handoff.bh_split_ledger_file_id == ledger.id
    assert len(handoff.drawings) == 2
    assert all(drawing.normal_dxf_file_id for drawing in handoff.drawings)
    assert all(drawing.weld_allowance_dxf_file_id for drawing in handoff.drawings)
    drawing_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "drawing_processing"
    )
    drawing_stage.job_attempt = 2
    db.flush()
    with pytest.raises(AppHTTPException) as exc_info:
        get_excel_split_handoff(db, workflow_id)
    assert exc_info.value.detail["code"] == "DXF_SPLIT_RUN_STALE"


def test_mixed_batch_finishes_every_file_and_review_zip_only_has_failed_originals(
    db,
    monkeypatch,
    tmp_path,
):
    _configure_local_storage(monkeypatch, tmp_path)
    workflow_id, job_id, sources = _split_job_fixture(
        db,
        tmp_path,
        parts=(("BH-REVIEW", "BH"), ("BOX-AUTO", "BOX"), ("PX-REVIEW", "PX")),
    )
    monkeypatch.setattr(
        split_execution,
        "_invoke_splitter",
        _fake_splitter(
            family_by_member={"BH-REVIEW": "BH", "BOX-AUTO": "BOX"},
            manual_members=frozenset({"BH-REVIEW"}),
        ),
    )

    split_execution.run_dxf_splitting(job_id, worker_name="test-split")

    db.expire_all()
    job = db.get(Job, job_id)
    run = db.scalar(select(DxfSplitRun).where(DxfSplitRun.job_id == job_id))
    assert job is not None and job.status == "succeeded"
    assert job.attempt == 1
    assert run is not None and run.status == "completed_with_review"
    assert run.input_count == 3
    assert run.auto_accepted_count == 1
    assert run.manual_review_count == 2
    assert len(run.items) == 3
    by_name = {item.source_name: item for item in run.items}
    assert by_name["BOX-AUTO_拆板前.dxf"].automation_route == "auto_accepted"
    assert by_name["BOX-AUTO_拆板前.dxf"].normal_dxf_file_id is not None
    assert by_name["BH-REVIEW_拆板前.dxf"].automation_route == "manual_review"
    assert by_name["PX-REVIEW_拆板前.dxf"].disposition == "unsupported_part_type"
    assert manual_review_archive_members(db, run) == [
        (sources["BH-REVIEW"], "BH-REVIEW_拆板前.dxf"),
        (sources["PX-REVIEW"], "PX-REVIEW_拆板前.dxf"),
    ]

    workflow = db.get(WorkflowRun, workflow_id)
    workflow_service.sync_workflow_from_jobs(db, workflow)
    assert workflow.current_stage == "drawing_processing"
    assert workflow.status == "waiting_review"
    drawing_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "drawing_processing"
    )
    assert drawing_stage.status == "waiting_review"
    db.commit()

    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    status = client.get(
        f"/api/v1/workflows/{workflow_id}/drawing-processing",
        headers=headers,
    )
    assert status.status_code == 200, status.text
    assert status.json()["data"]["status"] == "completed_with_review"
    archive = client.get(
        f"/api/v1/workflows/{workflow_id}/drawing-processing/runs/{run.id}/manual-review-archive",
        headers=headers,
    )
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(BytesIO(archive.content)) as zipped:
        names = zipped.namelist()
    assert names == [
        "BH-REVIEW_拆板前.dxf",
        "PX-REVIEW_拆板前.dxf",
    ]
    assert all(name.endswith(".dxf") for name in names)
    assert not any(name.endswith((".json", ".xlsx", ".png", ".dwg")) for name in names)
    drawing_stage.job_attempt = 2
    db.commit()
    stale_archive = client.get(
        f"/api/v1/workflows/{workflow_id}/drawing-processing/runs/{run.id}/manual-review-archive",
        headers=headers,
    )
    assert stale_archive.status_code == 404
    assert stale_archive.json()["error"]["code"] == "DXF_SPLIT_RUN_NOT_CURRENT"


def test_independent_validation_mismatch_becomes_business_review_not_job_failure(
    db,
    monkeypatch,
    tmp_path,
):
    _configure_local_storage(monkeypatch, tmp_path)
    _, job_id, _ = _split_job_fixture(
        db,
        tmp_path,
        parts=(("BH-BAD-PAIR", "BH"),),
    )
    monkeypatch.setattr(
        split_execution,
        "_invoke_splitter",
        _fake_splitter(
            family_by_member={"BH-BAD-PAIR": "BH"},
            invalid_members=frozenset({"BH-BAD-PAIR"}),
        ),
    )

    split_execution.run_dxf_splitting(job_id, worker_name="test-split")

    db.expire_all()
    job = db.get(Job, job_id)
    run = db.scalar(select(DxfSplitRun).where(DxfSplitRun.job_id == job_id))
    assert job is not None and job.status == "succeeded"
    assert run is not None and run.status == "completed_with_review"
    assert run.items[0].disposition == "independent_validation_failed"
    assert run.items[0].normal_dxf_file_id is None
    assert "INDEPENDENT_VALIDATION_FAILED" in run.items[0].diagnostics_json


def test_technical_failure_creates_at_most_three_immutable_attempts(
    db,
    monkeypatch,
    tmp_path,
):
    _configure_local_storage(monkeypatch, tmp_path)
    _, job_id, _ = _split_job_fixture(
        db,
        tmp_path,
        parts=(("BH-FAIL", "BH"),),
    )
    dispatched: list[tuple[int, int]] = []

    def fail_splitter(*_args, **_kwargs):
        raise split_adapter.DxfSplitError("fixture technical failure")

    monkeypatch.setattr(split_execution, "_invoke_splitter", fail_splitter)
    monkeypatch.setattr(
        split_execution,
        "dispatch_committed_job",
        lambda _db, job: dispatched.append((job.id, job.attempt)) or "task",
    )

    split_execution.run_dxf_splitting(
        job_id,
        worker_name="test-split",
        expected_attempt=1,
    )
    split_execution.run_dxf_splitting(
        job_id,
        worker_name="test-split",
        expected_attempt=2,
    )
    split_execution.run_dxf_splitting(
        job_id,
        worker_name="test-split",
        expected_attempt=3,
    )

    db.expire_all()
    job = db.get(Job, job_id)
    runs = db.scalars(
        select(DxfSplitRun).where(DxfSplitRun.job_id == job_id).order_by(DxfSplitRun.job_attempt)
    ).all()
    assert job is not None
    assert job.status == "failed"
    assert job.attempt == 3
    assert job.error_code == "DXF_SPLIT_FAILED"
    assert dispatched == [(job_id, 2), (job_id, 3)]
    assert [run.job_attempt for run in runs] == [1, 2, 3]
    assert [run.status for run in runs] == ["failed", "failed", "failed"]


def test_cancelled_job_closes_running_split_attempt(
    db,
    monkeypatch,
    tmp_path,
):
    _configure_local_storage(monkeypatch, tmp_path)
    _, job_id, _ = _split_job_fixture(
        db,
        tmp_path,
        parts=(("BH-CANCELLED", "BH"),),
    )

    def cancel_before_progress(session, target_job_id, **_kwargs):
        job = session.get(Job, target_job_id)
        assert job is not None
        job.status = "cancelled"
        session.commit()
        return None

    monkeypatch.setattr(
        split_execution,
        "commit_job_progress",
        cancel_before_progress,
    )

    split_execution.run_dxf_splitting(
        job_id,
        worker_name="test-split",
        expected_attempt=1,
    )

    db.expire_all()
    job = db.get(Job, job_id)
    run = db.scalar(
        select(DxfSplitRun).where(
            DxfSplitRun.job_id == job_id,
            DxfSplitRun.job_attempt == 1,
        )
    )
    assert job is not None and job.status == "cancelled"
    assert run is not None and run.status == "failed"
    assert run.error_code == "DXF_SPLIT_ATTEMPT_INTERRUPTED"
    assert run.finished_at is not None


def test_split_http_contract_is_exposed():
    paths = app.openapi()["paths"]
    assert "/api/v1/workflows/{workflow_id}/drawing-processing" in paths
    assert (
        "/api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive"
    ) in paths
