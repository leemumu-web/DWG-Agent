from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.modules.dxf_classification import persistence as classification_persistence
from app.modules.dxf_classification.interface import (
    list_pl_split_candidate_inputs,
    list_split_candidate_inputs,
)
from app.modules.dxf_splitting import pl_adapter


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
