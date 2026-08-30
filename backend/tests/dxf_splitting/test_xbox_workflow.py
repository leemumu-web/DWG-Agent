from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import ezdxf
from sqlalchemy import select

from app.modules.dxf_classification import persistence as classification_persistence
from app.modules.dxf_classification.interface import (
    DxfSplitCandidateInput,
    list_pl_split_candidate_inputs,
    list_split_candidate_inputs,
    list_xbox_split_candidate_inputs,
)
from app.modules.dxf_splitting import pl_execution, xbox_adapter
from app.modules.dxf_splitting.models import DxfSplitRun
from app.modules.dxf_splitting.validation import StagedSplitSource
from app.modules.dxf_splitting.xbox_validation import validate_xbox_result
from test_pl_workflow import (
    _classification_item,
    _success_report,
    _configure_local_storage,
    _prepare_pl_job,
    _save_test_file,
)

_XBOX_PROFILE = "XBOX300*500*50*30*50"
_WEB_WIDTH = 240.0  # 300 - 2*30
_FLANGE_WIDTH = 400.0  # 500 - 2*50


class _ClassificationDb:
    def __init__(self, files: dict[int, object]) -> None:
        self._files = files

    def get(self, _model, identity: int):
        return self._files.get(identity)


def test_xbox_candidates_are_isolated_from_pl_and_bh_box(monkeypatch) -> None:
    items = [
        _classification_item(1, "PL"),
        _classification_item(2, "XBOX"),
        _classification_item(3, "BH"),
        _classification_item(4, "BOX"),
    ]
    run = SimpleNamespace(items=items, classifier_version="1.3.0")
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

    xbox_inputs = list_xbox_split_candidate_inputs(db, 17)
    pl_inputs = list_pl_split_candidate_inputs(db, 17)
    legacy_inputs = list_split_candidate_inputs(db, 17)

    assert [item.part_type for item in xbox_inputs] == ["XBOX"]
    assert [item.part_type for item in pl_inputs] == ["PL"]
    assert [item.part_type for item in legacy_inputs] == ["BH", "BOX"]


def test_xbox_adapter_invokes_only_the_standalone_stage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    input_directory.mkdir()
    output_directory.mkdir()
    (input_directory / "XB-1.dxf").write_bytes(b"dxf")
    payload = {
        "schema": "steel-dxf-split-xbox-report/1",
        "input": str(input_directory),
        "output_dir": str(output_directory),
        "success_count": 0,
        "rejected_count": 1,
        "exit_code": 1,
        "items": [
            {
                "status": "manual_review",
                "source": str(input_directory / "XB-1.dxf"),
                "member": "XB-1",
                "outputs": {},
                "error": {"code": "TEST", "message_zh": "测试拒绝"},
            }
        ],
    }
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return subprocess.CompletedProcess(command, 1, json.dumps(payload), "")

    monkeypatch.setattr(xbox_adapter.subprocess, "run", fake_run)

    result = xbox_adapter.invoke_xbox_splitter(
        input_directory,
        output_directory,
        timeout_seconds=30,
    )

    assert result.payload == payload
    assert result.exit_code == 1
    assert "steel_dxf_split_xbox.cli" in captured
    assert "--authorize-project-tekla-xbox-dxf-v1" in captured
    assert not any(value == "steel_dxf_split.cli" for value in captured)
    assert not any(value == "steel_dxf_split_pl.cli" for value in captured)


def _staged_xbox_source(tmp_path: Path) -> StagedSplitSource:
    source = tmp_path / "input" / "XB-1_拆板前.dxf"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"source")
    return StagedSplitSource(
        semantic=DxfSplitCandidateInput(
            classification_item_id=1,
            drawing_id=None,
            classification_disposition="classified",
            part_type="XBOX",
            profile_normalized=_XBOX_PROFILE,
            type_source="catalog",
            source_file_id=101,
            output_file_id=201,
            classifier_version="1.3.0",
        ),
        source_name=source.name,
        staged_path=source,
    )


def _write_xbox_pair(
    normal_path: Path,
    weld_path: Path,
    *,
    length_mm: float = 10000.0,
    extension_mm: float = 10.0,
    member: str = "XB-1",
    web_label: str | None = None,
) -> None:
    def build(path: Path, plate_length: float) -> None:
        document = ezdxf.new("R2007")
        msp = document.modelspace()
        msp.add_lwpolyline(
            [(0, 0), (plate_length, 0), (plate_length, _WEB_WIDTH), (0, _WEB_WIDTH)],
            close=True,
        )
        msp.add_lwpolyline(
            [
                (0, 1000),
                (plate_length, 1000),
                (plate_length, 1000 + _FLANGE_WIDTH),
                (0, 1000 + _FLANGE_WIDTH),
            ],
            close=True,
        )
        label_text = web_label if web_label is not None else f"p={member}\\U+8179"
        msp.add_text(label_text, dxfattribs={"height": 90}).set_placement((10, 10))
        msp.add_text(f"p={member}\\U+7FFC", dxfattribs={"height": 90}).set_placement(
            (10, 1200)
        )
        document.header["$INSUNITS"] = 4
        document.saveas(path)

    build(normal_path, length_mm)
    build(weld_path, length_mm + extension_mm)


def _xbox_report_item(
    normal_path: Path,
    weld_path: Path,
) -> dict[str, object]:
    return {
        "source": normal_path.name,
        "member": "XB-1",
        "status": "auto_accepted",
        "family": "XBOX",
        "automation_route": "auto_accepted",
        "outputs": {
            "normal_dxf": "auto_accepted/xbox/XB-1/XB-1_正常拆板.dxf",
            "weld_allowance_dxf": "auto_accepted/xbox/XB-1/XB-1_余量增长.dxf",
            "report": "auto_accepted/xbox/XB-1/XB-1_report.json",
            "weld_allowance_report": "auto_accepted/xbox/XB-1/XB-1_weld_allowance_report.json",
        },
        "pair_proof": {"ok": True},
    }


def test_xbox_validation_accepts_certified_pair(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    task_dir = output_root / "auto_accepted" / "xbox" / "XB-1"
    task_dir.mkdir(parents=True)
    normal = task_dir / "XB-1_正常拆板.dxf"
    weld = task_dir / "XB-1_余量增长.dxf"
    (task_dir / "XB-1_report.json").write_text("{}", encoding="utf-8")
    (task_dir / "XB-1_weld_allowance_report.json").write_text("{}", encoding="utf-8")
    _write_xbox_pair(normal, weld)

    validated = validate_xbox_result(
        _staged_xbox_source(tmp_path),
        _xbox_report_item(normal, weld),
        output_root,
    )

    assert validated.automation_route == "auto_accepted"
    assert validated.family == "XBOX"
    assert validated.normal_dxf_path == normal.resolve()
    assert validated.weld_allowance_dxf_path == weld.resolve()
    assert validated.validation["checks"]["weld_allowance_tiers_proven"] is True
    assert validated.validation["checks"]["profile_widths_match"] is True


def test_xbox_validation_rejects_width_change_and_wrong_label(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    task_dir = output_root / "auto_accepted" / "xbox" / "XB-1"
    task_dir.mkdir(parents=True)
    normal = task_dir / "XB-1_正常拆板.dxf"
    weld = task_dir / "XB-1_余量增长.dxf"
    (task_dir / "XB-1_report.json").write_text("{}", encoding="utf-8")
    (task_dir / "XB-1_weld_allowance_report.json").write_text("{}", encoding="utf-8")
    _write_xbox_pair(normal, weld)
    # Break the pair: the weld web plate widens and the normal web label is wrong.
    weld_document = ezdxf.readfile(weld)
    for entity in weld_document.modelspace():
        if entity.dxftype() == "LWPOLYLINE":
            points = list(entity.get_points("xy"))
            if abs(max(p[1] for p in points) - _WEB_WIDTH) < 1e-6:
                stretched = [(x * 1.0, y * 1.005) for x, y in points]
                entity.set_points(stretched)
    weld_document.saveas(weld)
    normal_document = ezdxf.readfile(normal)
    for entity in normal_document.modelspace():
        if entity.dxftype() == "TEXT" and entity.dxf.text.endswith("\\U+8179"):
            entity.dxf.text = "p=WRONG\\U+8179"
    normal_document.saveas(normal)

    validated = validate_xbox_result(
        _staged_xbox_source(tmp_path),
        _xbox_report_item(normal, weld),
        output_root,
    )

    assert validated.automation_route == "manual_review"
    assert "XBOX_ALLOWANCE_WIDTH_CHANGED" in validated.diagnostics
    assert "XBOX_LABEL_INVALID" in validated.diagnostics


def test_xbox_validation_rejects_tier_mismatch(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    task_dir = output_root / "auto_accepted" / "xbox" / "XB-1"
    task_dir.mkdir(parents=True)
    normal = task_dir / "XB-1_正常拆板.dxf"
    weld = task_dir / "XB-1_余量增长.dxf"
    (task_dir / "XB-1_report.json").write_text("{}", encoding="utf-8")
    (task_dir / "XB-1_weld_allowance_report.json").write_text("{}", encoding="utf-8")
    # 10000.0mm boundary must take exactly +10; give it +15 instead.
    _write_xbox_pair(normal, weld, extension_mm=15.0)

    validated = validate_xbox_result(
        _staged_xbox_source(tmp_path),
        _xbox_report_item(normal, weld),
        output_root,
    )

    assert validated.automation_route == "manual_review"
    assert "XBOX_ALLOWANCE_TIER_MISMATCH" in validated.diagnostics


def test_pl_xbox_job_persists_paired_and_single_outputs(
    db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_local_storage(monkeypatch, tmp_path)
    workflow_id, job = _prepare_pl_job(
        db,
        tmp_path,
        parts=(("PL-1", "PL"), ("XB-1", "XBOX")),
    )

    def fake_pl_splitter(input_directory: Path, output_directory: Path, **_kwargs):
        [source] = tuple(input_directory.glob("*.dxf"))
        output = output_directory / "PL-1.dxf"
        _write_pl_result(output)
        payload = _success_report(output)
        payload["source"] = str(source.resolve())
        batch = {
            "schema": "steel-dxf-split-pl-report/2",
            "input": str(input_directory),
            "output_dir": str(output_directory),
            "success_count": 1,
            "rejected_count": 0,
            "exit_code": 0,
            "items": [payload],
        }
        return __import__(
            "app.modules.dxf_splitting.pl_adapter", fromlist=["PlSplitterResult"]
        ).PlSplitterResult(exit_code=0, payload=batch)

    def fake_xbox_splitter(input_directory: Path, output_directory: Path, **_kwargs):
        sources = sorted(input_directory.glob("*.dxf"), key=lambda p: p.name)
        assert len(sources) == 1
        source = sources[0]
        task_dir = output_directory / "auto_accepted" / "xbox" / "XB-1"
        task_dir.mkdir(parents=True)
        normal = task_dir / "XB-1_正常拆板.dxf"
        weld = task_dir / "XB-1_余量增长.dxf"
        (task_dir / "XB-1_report.json").write_text("{}", encoding="utf-8")
        (task_dir / "XB-1_weld_allowance_report.json").write_text("{}", encoding="utf-8")
        _write_xbox_pair(normal, weld)
        payload = {
            "status": "auto_accepted",
            "source": str(source.resolve()),
            "member": "XB-1",
            "family": "XBOX",
            "automation_route": "auto_accepted",
            "outputs": {
                "normal_dxf": "auto_accepted/xbox/XB-1/XB-1_正常拆板.dxf",
                "weld_allowance_dxf": "auto_accepted/xbox/XB-1/XB-1_余量增长.dxf",
                "report": "auto_accepted/xbox/XB-1/XB-1_report.json",
                "weld_allowance_report": "auto_accepted/xbox/XB-1/XB-1_weld_allowance_report.json",
            },
        }
        batch = {
            "schema": "steel-dxf-split-xbox-report/1",
            "input": str(input_directory),
            "output_dir": str(output_directory),
            "success_count": 1,
            "rejected_count": 0,
            "exit_code": 0,
            "items": [payload],
        }
        return xbox_adapter.XboxSplitterResult(exit_code=0, payload=batch)

    def _write_pl_result(path: Path) -> None:
        document = ezdxf.new("R2007")
        msp = document.modelspace()
        msp.add_lwpolyline(
            [(0, 0), (100.1, 0), (100.1, 50.0), (0, 50.0)],
            close=True,
            dxfattribs={"layer": "PLATE_CUT"},
        )
        msp.add_text("p=PL-1", dxfattribs={"layer": "PART_LABEL", "height": 8}).set_placement(
            (5, 5)
        )
        document.header["$INSUNITS"] = 4
        document.saveas(path)

    monkeypatch.setattr(pl_execution, "invoke_pl_splitter", fake_pl_splitter)
    monkeypatch.setattr(pl_execution, "invoke_xbox_splitter", fake_xbox_splitter)

    pl_execution.run_pl_dxf_splitting(job.id, worker_name="test-pl", expected_attempt=1)

    db.expire_all()
    completed_job = db.get(Job, job.id)
    run = db.scalar(select(DxfSplitRun).where(DxfSplitRun.job_id == job.id))
    assert completed_job is not None and completed_job.status == "succeeded"
    assert run is not None and run.status == "completed", (
        f"status={run.status} items=["
        + "; ".join(
            f"{i.family}:{i.automation_route}:{i.diagnostics_json}:{i.validation_json.get('findings')}"
            for i in run.items
        )
        + "]"
    )
    assert run.splitter_version == "pl-0.2.0;xbox-0.1.0"
    assert run.source_contracts_json == {
        "PL": "project_tekla_pl_dxf_v1",
        "XBOX": "project_tekla_xbox_dxf_v1",
    }
    assert run.auto_accepted_count == 2
    assert len(run.items) == 2
    items_by_family = {item.family: item for item in run.items}
    pl_item = items_by_family["PL"]
    xbox_item = items_by_family["XBOX"]
    # PL keeps the single-artifact rule.
    assert pl_item.normal_dxf_file_id is not None
    assert pl_item.weld_allowance_dxf_file_id is None
    assert pl_item.type_resolution == "classifier_confirmed"
    # XBOX registers the full pair.
    assert xbox_item.part_type == "XBOX"
    assert xbox_item.source_contract_id == "project_tekla_xbox_dxf_v1"
    assert xbox_item.normal_dxf_file_id is not None
    assert xbox_item.weld_allowance_dxf_file_id is not None
    assert xbox_item.normal_dxf_file_id != xbox_item.weld_allowance_dxf_file_id
    assert xbox_item.type_resolution == "classifier_confirmed"
    assert xbox_item.validation_json["status"] == "passed", (
        f"diagnostics={xbox_item.diagnostics_json} "
        f"findings={xbox_item.validation_json}"
    )


from app.modules.jobs.interface import Job  # noqa: E402  (used in assertions above)
