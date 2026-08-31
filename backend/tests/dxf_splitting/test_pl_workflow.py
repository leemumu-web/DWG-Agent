from __future__ import annotations

import json
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import ezdxf
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.dxf_classification import persistence as classification_persistence
from app.modules.dxf_classification.interface import (
    DxfSplitCandidateInput,
    list_pl_split_candidate_inputs,
    list_split_candidate_inputs,
)
from app.modules.dxf_classification.models import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.dxf_splitting import pl_adapter, pl_execution
from app.modules.dxf_splitting.models import DxfSplitRun
from app.modules.dxf_splitting.persistence import PL_XBOX_COMBINED_CLI_SCHEMA
from app.modules.dxf_splitting.pl_validation import validate_pl_result
from app.modules.dxf_splitting.validation import StagedSplitSource
from app.modules.files.interface import clear_storage_backend_cache, save_bytes_as_file
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows import interface as workflow_service
from app.modules.workflows.interface import WorkflowRun
from app.modules.workflows.schemas import WorkflowCreate, WorkflowStageExecutionCreate
from app.modules.workflows.templates import WORKFLOW_TEMPLATES
from app.platform.config.settings import settings


class _ClassificationDb:
    def __init__(self, files: dict[int, object]) -> None:
        self._files = files

    def get(self, _model, identity: int):
        return self._files.get(identity)


def _classification_item(identity: int, part_type: str | None, **overrides):
    values = {
        "id": identity,
        "drawing_id": None,
        "next_stage_eligible": True,
        "disposition": "classified",
        "part_type": part_type,
        "profile_normalized": f"{part_type}-PROFILE" if part_type else None,
        "type_source": "catalog" if part_type else None,
        "source_file_id": identity + 100,
        "output_file_id": identity + 200,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pl_candidates_are_isolated_from_existing_bh_box_candidates(monkeypatch) -> None:
    items = [
        _classification_item(1, "PL"),
        _classification_item(2, "XBOX"),
        _classification_item(3, "BH"),
        _classification_item(4, "BOX"),
        _classification_item(
            5,
            None,
            next_stage_eligible=False,
            disposition="review_required",
        ),
    ]
    run = SimpleNamespace(items=items, classifier_version="1.2.0")
    files = {
        item.output_file_id: SimpleNamespace(status="available", file_ext=".dxf")
        for item in items
    }
    db = _ClassificationDb(files)
    monkeypatch.setattr(
        classification_persistence,
        "latest_classification_run",
        lambda _db, _workflow_id: run,
    )

    pl_inputs = list_pl_split_candidate_inputs(db, 17)
    legacy_inputs = list_split_candidate_inputs(db, 17)

    assert [item.part_type for item in pl_inputs] == ["PL"]
    assert [item.part_type for item in legacy_inputs] == ["BH", "BOX"]


def test_pl_adapter_invokes_only_the_standalone_stage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    input_directory.mkdir()
    output_directory.mkdir()
    (input_directory / "PL-1.dxf").write_bytes(b"dxf")
    payload = {
        "schema": "steel-dxf-split-pl-report/2",
        "input": str(input_directory),
        "output_dir": str(output_directory),
        "report": str(output_directory / "pl_split_report.json"),
        "success_count": 0,
        "rejected_count": 1,
        "exit_code": 1,
        "items": [
            {
                "status": "rejected",
                "source": str(input_directory / "PL-1.dxf"),
                "context_id": "modelspace",
                "part_number": None,
                "error": {"code": "TEST", "message_zh": "测试拒绝"},
            }
        ],
    }
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return subprocess.CompletedProcess(command, 1, json.dumps(payload), "")

    monkeypatch.setattr(pl_adapter.subprocess, "run", fake_run)

    result = pl_adapter.invoke_pl_splitter(
        input_directory,
        output_directory,
        timeout_seconds=30,
    )

    assert result.payload == payload
    assert result.exit_code == 1
    assert "steel_dxf_split_pl.cli" in captured
    assert "--authorize-project-tekla-pl-dxf-v1" in captured
    assert not any(value == "steel_dxf_split.cli" for value in captured)


def _staged_pl_source(tmp_path: Path) -> StagedSplitSource:
    source = tmp_path / "input" / "PL-1.dxf"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"source")
    return StagedSplitSource(
        semantic=DxfSplitCandidateInput(
            classification_item_id=1,
            drawing_id=None,
            classification_disposition="classified",
            part_type="PL",
            profile_normalized="PL10",
            type_source="catalog",
            source_file_id=101,
            output_file_id=201,
            classifier_version="1.2.0",
        ),
        source_name=source.name,
        staged_path=source,
    )


def _write_pl_output(
    path: Path,
    *,
    length_mm: float = 100.1,
    width_mm: float = 50.0,
    label: str = "p=PL-1",
    close_outline: bool = True,
) -> None:
    document = ezdxf.new("R2007")
    document.header["$INSUNITS"] = 4
    modelspace = document.modelspace()
    points = ((0, 0), (length_mm, 0), (length_mm, width_mm), (0, width_mm))
    for start, end in zip(points, (*points[1:], points[0]), strict=True):
        if not close_outline and start == points[-1]:
            continue
        modelspace.add_line(start, end, dxfattribs={"layer": "PLATE_CUT"})
    modelspace.add_text(label, dxfattribs={"layer": "PART_LABEL"})
    document.saveas(path)


def _success_report(output: Path) -> dict[str, object]:
    return {
        "status": "success",
        "source": str(output.parent.parent / "input" / "PL-1.dxf"),
        "context_id": "modelspace",
        "part_number": "PL-1",
        "metadata": {
            "thickness_mm": 10.0,
            "width_mm": 50.0,
            "bom_length_mm": 100.01,
        },
        "lengths": {
            "projection_mm": 100.0,
            "k_length_mm": None,
            "bom_mm": 100.01,
            "raw_mm": 100.01,
            "target_mm": 100.1,
        },
        "output": {
            "path": str(output),
            "label": "p=PL-1",
            "length_mm": 100.1,
            "width_mm": 50.0,
            "audit_error_count": 0,
            "shapely_closed_valid": True,
        },
    }


def _configure_local_storage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "dxf_split_work_root", tmp_path / "split-work")
    clear_storage_backend_cache()


def _save_test_file(
    db,
    *,
    user_id: int,
    original_name: str,
    payload: bytes,
    report: bool = False,
):
    extension = Path(original_name).suffix.casefold()
    return save_bytes_as_file(
        db,
        bucket=(
            settings.minio_bucket_reports
            if report
            else settings.minio_bucket_dxf_derived
        ),
        storage_key=f"tests/pl-split-input/{uuid4().hex}{extension}",
        original_name=original_name,
        file_ext=extension,
        content_type="application/json" if report else "application/dxf",
        payload=payload,
        uploaded_by=user_id,
        batch_name="pl-classification-ledger" if report else "pl-classified-input",
    )


def _prepare_pl_job(db, tmp_path: Path, *, parts):
    user = User(
        username=f"pl-split-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="PL Split Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"PL-SPLIT-{uuid4().hex[:6]}",
        name="PL DXF split",
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
            name="PL split batch",
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
    report = _save_test_file(
        db,
        user_id=user.id,
        original_name="分类报告.json",
        payload=b'{"schema":"STEEL-DXF-CLASSIFICATION-1.2"}',
        report=True,
    )
    manifest = _save_test_file(
        db,
        user_id=user.id,
        original_name="分类清单.csv",
        payload=b"name,status\n",
        report=True,
    )
    classification = DxfClassificationRun(
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
    db.add(classification)
    db.flush()
    source_path = tmp_path / f"source-{uuid4().hex}.dxf"
    source_document = ezdxf.new("R2010")
    source_document.modelspace().add_line((0, 0), (100, 0))
    source_document.saveas(source_path)
    source_payload = source_path.read_bytes()
    for member, part_type in parts:
        source = _save_test_file(
            db,
            user_id=user.id,
            original_name=f"{member}_拆板前.dxf",
            payload=source_payload,
        )
        db.add(
            DxfClassificationItem(
                run=classification,
                source_file_id=source.id,
                output_file_id=source.id,
                source_name=source.original_name,
                output_name=source.original_name,
                output_directory=f"{classification.project_name}_{part_type}_dxf",
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
            metadata={
                "job_id": classification_job.id,
                "job_attempt": classification_job.attempt,
            },
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
            metadata={
                "job_id": classification_job.id,
                "job_attempt": classification_job.attempt,
            },
        )
    source_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "source_intake"
    )
    classification_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "dxf_classification"
    )
    pl_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "pl_xbox_split"
    )
    source_stage.status = "succeeded"
    source_stage.progress = 100
    classification_stage.status = "succeeded"
    classification_stage.progress = 100
    classification_stage.job_id = classification_job.id
    classification_stage.job_attempt = classification_job.attempt
    pl_stage.status = "waiting_input"
    workflow.current_stage = "pl_xbox_split"
    workflow.status = "waiting_input"
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="split_pl_dxf",
        pipeline="pl_dxf_split",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json={
            "workflow_id": workflow.id,
            "classification_run_id": classification.id,
            "input_manifest_sha256": classification.input_manifest_sha256,
        },
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(
        db,
        workflow,
        stage_code="pl_xbox_split",
        job=job,
    )
    db.commit()
    return workflow.id, job


def test_pl_saved_output_is_independently_validated(tmp_path: Path) -> None:
    source = _staged_pl_source(tmp_path)
    output_root = tmp_path / "output"
    output_root.mkdir()
    output = output_root / "PL-1.dxf"
    _write_pl_output(output)

    validated = validate_pl_result(source, _success_report(output), output_root)

    assert validated.automation_route == "auto_accepted"
    assert validated.family == "PL"
    assert validated.normal_dxf_path == output.resolve()
    assert validated.weld_allowance_dxf_path is None
    assert validated.validation["checks"]["target_is_upward_tenth"] is True


def test_pl_validation_rejects_downward_length(tmp_path: Path) -> None:
    source = _staged_pl_source(tmp_path)
    output_root = tmp_path / "output"
    output_root.mkdir()
    output = output_root / "PL-1.dxf"
    _write_pl_output(output, length_mm=100.0)

    validated = validate_pl_result(source, _success_report(output), output_root)

    assert validated.automation_route == "manual_review"
    assert "PL_OUTPUT_LENGTH_DOWNWARD" in validated.diagnostics


def test_pl_validation_rejects_open_outline_and_wrong_label(tmp_path: Path) -> None:
    source = _staged_pl_source(tmp_path)
    output_root = tmp_path / "output"
    output_root.mkdir()
    output = output_root / "PL-1.dxf"
    _write_pl_output(output, label="PL-1", close_outline=False)

    validated = validate_pl_result(source, _success_report(output), output_root)

    assert validated.automation_route == "manual_review"
    assert "PL_OUTPUT_OUTLINE_INVALID" in validated.diagnostics
    assert "PL_OUTPUT_LABEL_INVALID" in validated.diagnostics


def test_production_workflow_runs_pl_before_legacy_bh_box_split() -> None:
    stages = WORKFLOW_TEMPLATES["linux_production"].stages
    codes = [stage.code for stage in stages]

    assert codes.index("dxf_classification") + 1 == codes.index("pl_xbox_split")
    assert codes.index("pl_xbox_split") + 1 == codes.index("drawing_processing")
    capability = next(stage for stage in stages if stage.code == "pl_xbox_split")
    assert capability.execution_kind == "pl_xbox_split"
    assert capability.implementation_status == "implemented"
    assert WorkflowStageExecutionCreate(
        execution_kind="pl_xbox_split"
    ).execution_kind == "pl_xbox_split"


def test_pl_job_persists_only_independently_accepted_normal_output(
    db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_local_storage(monkeypatch, tmp_path)
    workflow_id, job = _prepare_pl_job(
        db,
        tmp_path,
        parts=(("PL-1", "PL"),),
    )

    def fake_splitter(input_directory: Path, output_directory: Path, **_kwargs):
        [source] = tuple(input_directory.glob("*.dxf"))
        output = output_directory / "PL-1.dxf"
        _write_pl_output(output)
        payload = _success_report(output)
        payload["source"] = str(source.resolve())
        batch = {
            "schema": "steel-dxf-split-pl-report/2",
            "input": str(input_directory),
            "output_dir": str(output_directory),
            "report": str(output_directory / "pl_split_report.json"),
            "success_count": 1,
            "rejected_count": 0,
            "exit_code": 0,
            "items": [payload],
        }
        return pl_adapter.PlSplitterResult(exit_code=0, payload=batch)

    monkeypatch.setattr(pl_execution, "invoke_pl_splitter", fake_splitter)

    pl_execution.run_pl_dxf_splitting(job.id, worker_name="test-pl", expected_attempt=1)

    db.expire_all()
    completed_job = db.get(Job, job.id)
    run = db.scalar(select(DxfSplitRun).where(DxfSplitRun.job_id == job.id))
    workflow = db.get(WorkflowRun, workflow_id)
    assert completed_job is not None and completed_job.status == "succeeded"
    assert run is not None and run.status == "completed"
    assert run.splitter_version == "pl-0.2.0;xbox-0.1.0"
    assert run.source_contracts_json == {
        "PL": "project_tekla_pl_dxf_v1",
        "XBOX": "project_tekla_xbox_dxf_v1",
    }
    assert run.auto_accepted_count == 1
    assert run.manual_review_count == 0
    assert len(run.items) == 1
    item = run.items[0]
    assert item.family == "PL"
    assert item.type_resolution == "classifier_confirmed"
    assert item.normal_dxf_file_id is not None
    assert item.weld_allowance_dxf_file_id is None
    assert workflow is not None
    pl_artifacts = [
        artifact
        for artifact in workflow.artifacts
        if artifact.stage is not None and artifact.stage.stage_code == "pl_xbox_split"
    ]
    assert any(artifact.artifact_type == "processed_dxf" for artifact in pl_artifacts)
    assert not any(artifact.artifact_type == "weld_allowance_dxf" for artifact in pl_artifacts)


def test_pl_job_routes_an_entire_rejected_batch_to_manual_review(
    db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_local_storage(monkeypatch, tmp_path)
    _workflow_id, job = _prepare_pl_job(
        db,
        tmp_path,
        parts=(("PL-1", "PL"), ("PL-2", "PL")),
    )

    def fake_splitter(input_directory: Path, output_directory: Path, **_kwargs):
        items = [
            {
                "status": "rejected",
                "source": str(source.resolve()),
                "context_id": source.stem,
                "part_number": source.stem.split("_", maxsplit=1)[0],
                "error": {
                    "code": "DXF_LOAD_FAILED",
                    "message_zh": "测试逐图安全拒绝",
                },
            }
            for source in sorted(input_directory.glob("*.dxf"))
        ]
        return pl_adapter.PlSplitterResult(
            exit_code=1,
            payload={
                "schema": "steel-dxf-split-pl-report/2",
                "input": str(input_directory),
                "output_dir": str(output_directory),
                "report": str(output_directory / "pl_split_report.json"),
                "success_count": 0,
                "rejected_count": len(items),
                "exit_code": 1,
                "items": items,
            },
        )

    monkeypatch.setattr(pl_execution, "invoke_pl_splitter", fake_splitter)

    pl_execution.run_pl_dxf_splitting(
        job.id,
        worker_name="test-pl",
        expected_attempt=1,
    )

    db.expire_all()
    completed_job = db.get(Job, job.id)
    run = db.scalar(select(DxfSplitRun).where(DxfSplitRun.job_id == job.id))
    assert completed_job is not None and completed_job.status == "succeeded"
    assert run is not None and run.status == "completed_with_review"
    assert run.processed_count == 2
    assert run.auto_accepted_count == 0
    assert run.manual_review_count == 2
    assert len(run.items) == 2
    assert {item.automation_route for item in run.items} == {"manual_review"}
    assert {item.disposition for item in run.items} == {"stage_rejected"}
    assert all(item.normal_dxf_file_id is None for item in run.items)


def test_pl_http_contract_exports_normal_results_and_failed_sources_only(
    db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_local_storage(monkeypatch, tmp_path)
    workflow_id, job = _prepare_pl_job(
        db,
        tmp_path,
        parts=(("PL-1", "PL"), ("PL-2", "PL")),
    )

    def fake_splitter(input_directory: Path, output_directory: Path, **_kwargs):
        items: list[dict[str, object]] = []
        for source in sorted(input_directory.glob("*.dxf")):
            if source.name.startswith("PL-1"):
                output = output_directory / "PL-1.dxf"
                _write_pl_output(output)
                payload = _success_report(output)
                payload["source"] = str(source.resolve())
                items.append(payload)
            else:
                items.append(
                    {
                        "status": "rejected",
                        "source": str(source.resolve()),
                        "context_id": "modelspace",
                        "part_number": "PL-2",
                        "error": {
                            "code": "PL_PROFILE_UNSAFE",
                            "message_zh": "测试安全拒绝",
                        },
                    }
                )
        return pl_adapter.PlSplitterResult(
            exit_code=1,
            payload={
                "schema": "steel-dxf-split-pl-report/2",
                "input": str(input_directory),
                "output_dir": str(output_directory),
                "report": str(output_directory / "pl_split_report.json"),
                "success_count": 1,
                "rejected_count": 1,
                "exit_code": 1,
                "items": items,
            },
        )

    monkeypatch.setattr(pl_execution, "invoke_pl_splitter", fake_splitter)
    pl_execution.run_pl_dxf_splitting(job.id, worker_name="test-pl", expected_attempt=1)
    db.expire_all()
    run = db.scalar(select(DxfSplitRun).where(DxfSplitRun.job_id == job.id))
    assert run is not None and run.status == "completed_with_review"

    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    status_response = client.get(
        f"/api/v1/workflows/{workflow_id}/pl-xbox-split",
        headers=headers,
    )
    assert status_response.status_code == 200, status_response.text
    public_run = status_response.json()["data"]
    assert public_run["id"] == run.id
    assert public_run["source_contracts"] == {
        "PL": "project_tekla_pl_dxf_v1",
        "XBOX": "project_tekla_xbox_dxf_v1",
    }
    assert public_run["split_ledger_file"] is not None
    assert all(item["family"] == "PL" for item in public_run["items"])
    assert all(item["weld_allowance_dxf_file_id"] is None for item in public_run["items"])

    preview = client.get(
        f"/api/v1/workflows/{workflow_id}/pl-xbox-split/runs/{run.id}"
        "/selective-export-preview",
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert [
        (item["key"], item["label"], item["file_count"])
        for item in preview.json()["data"]["categories"]
    ] == [
        ("failed_pl", "未通过的 PL", 1),
        ("failed_xbox", "未通过的 XBOX", 0),
        ("other", "其他", 0),
    ]

    created = client.post(
        f"/api/v1/workflows/{workflow_id}/pl-xbox-split/runs/{run.id}"
        "/selective-exports",
        headers=headers,
        json={"categories": ["failed_pl"]},
    )
    assert created.status_code == 201, created.text
    prepared = created.json()["data"]
    archive = client.get(prepared["download_url"])
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(BytesIO(archive.content)) as zipped:
        assert zipped.namelist() == ["未通过的PL/PL-2_拆板前.dxf"]

    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/batch-exports",
        headers=headers,
        json={"categories": ["split_result_normal"]},
    )
    assert batch.status_code == 201, batch.text
    assert batch.json()["data"]["file_count"] == 1

    workflow = db.get(WorkflowRun, workflow_id)
    assert workflow is not None
    pl_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "pl_xbox_split"
    )
    pl_stage.job_attempt = 2
    db.commit()
    stale = client.get(
        f"/api/v1/workflows/{workflow_id}/pl-xbox-split/runs/{run.id}"
        "/selective-export-preview",
        headers=headers,
    )
    assert stale.status_code == 404
    assert stale.json()["error"]["code"] == "PL_SPLIT_RUN_NOT_CURRENT"


def test_pl_xbox_combined_cli_schema_fits_run_column() -> None:
    """The combined PL+XBOX CLI schema must fit dxf_split_runs.cli_schema.

    Regression: the pl:/xbox:-prefixed form was 66 chars, overflowing the
    varchar(64) column and failing every merged run with
    DataError (1406 "Data too long for column 'cli_schema'").
    """
    assert len(PL_XBOX_COMBINED_CLI_SCHEMA) <= 64
    assert PL_XBOX_COMBINED_CLI_SCHEMA == (
        "steel-dxf-split-pl-report/2;steel-dxf-split-xbox-report/1"
    )
    # The report schema IDs that feed the combined value stay stable and the
    # family names are still carried inside them.
    assert "steel-dxf-split-pl-report/2" in PL_XBOX_COMBINED_CLI_SCHEMA
    assert "steel-dxf-split-xbox-report/1" in PL_XBOX_COMBINED_CLI_SCHEMA
