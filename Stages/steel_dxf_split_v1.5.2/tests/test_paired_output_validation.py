from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.paired_output import (
    PairedOutputValidationError,
    validate_paired_outputs,
)


def _drawing(
    path: Path,
    *,
    right_color: int = 7,
    units: int = 4,
    include_right_hole: bool = True,
) -> None:
    document = ezdxf.new("R2007")
    document.header["$INSUNITS"] = units
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
        close=True,
        dxfattribs={"layer": "PLATE_CUT"},
    )
    modelspace.add_circle(
        (25.0, 25.0),
        5.0,
        dxfattribs={"layer": "CUT_HOLE", "color": 1},
    )
    if include_right_hole:
        modelspace.add_circle(
            (75.0, 25.0),
            5.0,
            dxfattribs={"layer": "CUT_HOLE", "color": right_color},
        )
    document.saveas(path)


def _allowance_report(normal: Path, allowance: Path, report: Path) -> None:
    report.write_text(
        json.dumps(
            {
                "ok": True,
                "input_split_dxf": str(normal.resolve()),
                "output_dxf": str(allowance.resolve()),
                "original_split_result_preserved": True,
                "checks": {"saved_dxf_audit_clean": True},
                "plates": [
                    {
                        "before_main_length_mm": 6000.0,
                        "allowance_mm": 10.0,
                        "after_main_length_mm": 6010.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_pair_validation_proves_hole_colors_are_identical_in_both_versions(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.dxf"
    allowance = tmp_path / "allowance.dxf"
    report = tmp_path / "allowance.json"
    _drawing(normal)
    _drawing(allowance)
    _allowance_report(normal, allowance, report)

    validation = validate_paired_outputs(normal, allowance, report, family="BH")

    assert validation["ok"] is True
    assert validation["checks"]["cut_hole_geometry_and_colors_identical"] is True
    assert validation["positive_extension_count"] == 1


def test_pair_validation_rejects_a_white_partner_recolored_in_allowance_version(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.dxf"
    allowance = tmp_path / "allowance.dxf"
    report = tmp_path / "allowance.json"
    _drawing(normal)
    _drawing(allowance, right_color=1)
    _allowance_report(normal, allowance, report)

    with pytest.raises(PairedOutputValidationError, match="hole geometry or color"):
        validate_paired_outputs(normal, allowance, report, family="BH")


def test_pair_validation_rejects_report_bound_to_another_output_pair(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.dxf"
    allowance = tmp_path / "allowance.dxf"
    wrong_normal = tmp_path / "wrong-normal.dxf"
    report = tmp_path / "allowance.json"
    _drawing(normal)
    _drawing(allowance)
    _drawing(wrong_normal)
    _allowance_report(wrong_normal, allowance, report)

    with pytest.raises(PairedOutputValidationError, match="not bound"):
        validate_paired_outputs(normal, allowance, report, family="BH")


def test_pair_validation_rejects_different_dxf_units(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.dxf"
    allowance = tmp_path / "allowance.dxf"
    report = tmp_path / "allowance.json"
    _drawing(normal, units=4)
    _drawing(allowance, units=1)
    _allowance_report(normal, allowance, report)

    with pytest.raises(PairedOutputValidationError, match="contracts differ"):
        validate_paired_outputs(normal, allowance, report, family="BH")


def test_pair_validation_rejects_missing_entities(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.dxf"
    allowance = tmp_path / "allowance.dxf"
    report = tmp_path / "allowance.json"
    _drawing(normal)
    _drawing(allowance, include_right_hole=False)
    _allowance_report(normal, allowance, report)

    with pytest.raises(PairedOutputValidationError, match="entity sets differ"):
        validate_paired_outputs(normal, allowance, report, family="BH")


def test_pair_validation_rejects_box_report_without_verified_groups(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.dxf"
    allowance = tmp_path / "allowance.dxf"
    report = tmp_path / "allowance.json"
    _drawing(normal)
    _drawing(allowance)
    _allowance_report(normal, allowance, report)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["groups"] = []
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PairedOutputValidationError, match="no verified plate groups"):
        validate_paired_outputs(normal, allowance, report, family="BOX")
