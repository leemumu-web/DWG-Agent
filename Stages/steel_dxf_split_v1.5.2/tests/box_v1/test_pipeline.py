from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.box import delivery
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.release import write_box_release_attestation
from steel_dxf_split.pipeline import SplitOptions, split_dxf
from tests.box_v1.paths import INPUTS

INPUT = next(INPUTS.glob("2b1-cb-56*.dxf"))


@pytest.fixture(autouse=True)
def _small_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delivery.box_preview, "PREVIEW_DPI", 40)
    monkeypatch.setattr(delivery.box_preview, "PREVIEW_CANVAS_PIXELS", (600, 420))
    monkeypatch.setattr(delivery.box_preview, "PREVIEW_MAX_PIXELS", 300_000)


def _release(path: Path) -> Path:
    write_box_release_attestation(
        path,
        pair_count=20,
        calibration_count=10,
        acceptance_count=10,
        manifest_fingerprint="a" * 64,
        gate_fingerprint="b" * 64,
    )
    return path


def _options(release_path: Path) -> SplitOptions:
    return SplitOptions(
        box_source_contract=BoxSourceContract(),
        box_release_attestation=release_path,
    )


def test_unified_worker_box_route_writes_valid_transaction(tmp_path: Path) -> None:
    release_path = _release(tmp_path / "release.json")

    result = split_dxf(INPUT, tmp_path / "output", _options(release_path))

    assert result.production_path is not None
    assert result.production_path.is_file()
    assert result.weld_allowance_path is not None
    assert result.weld_allowance_path.is_file()
    assert result.review_path is None
    assert result.report_path.is_file()
    assert result.report["profile_family"] == "BOX"
    assert result.report["report_schema"] == "BOX-COMPILATION-REPORT-4.0"
    assert result.report["automation_route"] == "auto_accepted"
    assert result.report["manufacturing_ir_validation"]["ok"]
    assert result.report["saved_dxf"]["ok"]
    assert result.report["proof_report"]["disposition"] == "auto_accept"
    assert result.report["ground_truth_used_for_decision"] is False
    assert result.report["legacy_solver_called"] is False
    groups = result.report["weld_allowance_output_groups"]
    assert groups
    assert all(group["contract"] is not None for group in groups)
    assert all(len(group["contract_sha256"]) == 64 for group in groups)
    previews = result.report["outputs"]["previews"]
    assert Path(previews["before"]).is_file()
    assert Path(previews["after"]).is_file()


def test_unified_worker_box_writer_bytes_are_deterministic(
    tmp_path: Path,
) -> None:
    release_path = _release(tmp_path / "release.json")
    options = _options(release_path)

    first = split_dxf(INPUT, tmp_path / "first", options)
    second = split_dxf(INPUT, tmp_path / "second", options)

    assert first.production_path is not None
    assert second.production_path is not None
    assert first.production_path.read_bytes() == second.production_path.read_bytes()


def test_unified_worker_report_json_is_the_persisted_result(tmp_path: Path) -> None:
    release_path = _release(tmp_path / "release.json")
    result = split_dxf(INPUT, tmp_path / "output", _options(release_path))

    assert json.loads(result.report_path.read_text(encoding="utf-8")) == result.report


def test_failed_recompile_restores_previous_official_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_path = _release(tmp_path / "release.json")
    output = tmp_path / "output"
    options = _options(release_path)
    first = split_dxf(INPUT, output, options)
    assert first.production_path is not None
    protected = {
        path: path.read_bytes()
        for path in (
            first.production_path,
            first.report_path,
            Path(first.report["outputs"]["previews"]["before"]),
            Path(first.report["outputs"]["previews"]["after"]),
        )
    }
    original_write = delivery.write_box_clean

    def write_then_delete_plate(manufacturing_ir, output_path, **kwargs):
        layout = original_write(manufacturing_ir, output_path, **kwargs)
        document = ezdxf.readfile(output_path)
        modelspace = document.modelspace()
        modelspace.delete_entity(
            modelspace.query("LWPOLYLINE[layer=='PLATE_CUT']")[0]
        )
        document.saveas(output_path)
        return layout

    monkeypatch.setattr(delivery, "write_box_clean", write_then_delete_plate)

    with pytest.raises(ValueError, match="BOX saved DXF validation failed"):
        split_dxf(INPUT, output, options)

    assert {path: path.read_bytes() for path in protected} == protected
