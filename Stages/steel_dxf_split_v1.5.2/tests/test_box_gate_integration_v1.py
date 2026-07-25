from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.box import delivery
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.release import write_box_release_attestation
from steel_dxf_split.box.weld_allowance import _cut_geometry as _box_cut_geometry
from steel_dxf_split.pipeline import SplitOptions, split_dxf

ROOT = Path(__file__).resolve().parents[1]
BOX_SOURCE = (
    ROOT
    / "samples"
    / "box_pairs"
    / "BOX_拆板前_dxf"
    / "2b1-cb-56_拆板前.dxf"
)


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


def test_packaged_release_enables_native_box_production_output(
    tmp_path: Path,
) -> None:
    result = split_dxf(
        BOX_SOURCE,
        tmp_path / "output",
        SplitOptions(
            box_source_contract=BoxSourceContract(),
        ),
    )

    assert result.clean_path is not None and result.clean_path.is_file()
    assert result.review_path is None
    assert result.sheet_path is None
    assert result.report["automation_route"] == "auto_accepted"
    assert result.report["single_file_disposition"] == "auto_accept"
    release_attestation = result.report["release_attestation"]
    assert release_attestation["passed"] is True
    assert Path(release_attestation["release_path"]).name == (
        "box_release_attestation.json"
    )


def test_current_release_enables_native_box_production_output(
    tmp_path: Path,
) -> None:
    release_path = _release(tmp_path / "release.json")
    result = split_dxf(
        BOX_SOURCE,
        tmp_path / "output",
        SplitOptions(
            box_source_contract=BoxSourceContract(),
            box_release_attestation=release_path,
        ),
    )

    assert result.clean_path is not None and result.clean_path.is_file()
    assert result.review_path is None
    assert result.sheet_path is None
    assert result.report["automation_route"] == "auto_accepted"
    assert result.report["saved_dxf"]["ok"] is True
    assert result.weld_allowance_path is not None
    assert result.weld_allowance_path.is_file()
    assert result.weld_allowance_report_path is not None
    assert result.weld_allowance_report_path.is_file()
    assert result.report["paired_output"]["validation"]["ok"] is True
    assert result.report["paired_output"]["validation"]["positive_extension_count"] > 0
    assert result.task_dir == (
        tmp_path / "output" / "auto_accepted" / "box" / "2b1-cb-56"
    )
    assert sorted(path.name for path in result.task_dir.glob("*.dxf")) == [
        "2b1-cb-56_余量增长.dxf",
        "2b1-cb-56_正常拆板.dxf",
    ]
    allowance_report = json.loads(
        result.weld_allowance_report_path.read_text(encoding="utf-8")
    )
    assert allowance_report["original_split_result_preserved"] is True
    assert all(allowance_report["checks"].values())
    assert any(group["allowance_mm"] > 0.0 for group in allowance_report["groups"])
    normal = ezdxf.readfile(result.production_path)
    allowance = ezdxf.readfile(result.weld_allowance_path)
    assert _box_cut_geometry(normal) == _box_cut_geometry(allowance)
    modelspace = normal.modelspace()
    assert modelspace.query("LWPOLYLINE[layer=='PLATE_CUT']")
    assert not modelspace.query("REGION")


def test_invalid_packaged_release_fails_closed_without_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"

    def reject_packaged_release(_path: Path | None = None):
        raise ValueError("BOX 内置 release attestation 缺失或无效")

    monkeypatch.setattr(
        "steel_dxf_split.box.release.load_verified_box_release_attestation",
        reject_packaged_release,
    )

    with pytest.raises(ValueError, match="内置 release attestation"):
        split_dxf(
            BOX_SOURCE,
            output,
            SplitOptions(
                box_source_contract=BoxSourceContract(),
            ),
        )

    assert not list(output.rglob("*"))
