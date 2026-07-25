from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split import dxf_preview
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.pipeline import SplitOptions, split_dxf
from steel_dxf_split.weld_allowance import _cut_geometry


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "samples" / "bh_pairs" / "2b1-cb-29_拆板前.dxf"


def test_real_bh_auto_accept_generates_verified_allowance_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dxf_preview, "PREVIEW_CANVAS_PIXELS", (320, 224))

    result = split_dxf(
        SOURCE,
        tmp_path / "output",
        SplitOptions(source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT),
    )

    assert result.automation_route == "auto_accepted"
    assert result.production_path is not None and result.production_path.is_file()
    assert result.weld_allowance_path is not None
    assert result.weld_allowance_path.is_file()
    assert result.weld_allowance_report_path is not None
    assert result.weld_allowance_report_path.is_file()
    assert result.weld_allowance_path.parent == result.production_path.parent
    assert result.task_dir == (
        tmp_path / "output" / "auto_accepted" / "bh" / "2b1-cb-29"
    )
    assert sorted(path.name for path in result.task_dir.glob("*.dxf")) == [
        "2b1-cb-29_余量增长.dxf",
        "2b1-cb-29_正常拆板.dxf",
    ]
    allowance_report = json.loads(
        result.weld_allowance_report_path.read_text(encoding="utf-8")
    )
    assert allowance_report["original_split_result_preserved"] is True
    assert all(allowance_report["checks"].values())
    assert any(plate["allowance_mm"] > 0.0 for plate in allowance_report["plates"])
    clean = ezdxf.readfile(result.production_path)
    allowance = ezdxf.readfile(result.weld_allowance_path)
    assert _cut_geometry(clean) == _cut_geometry(allowance)
    assert result.report["outputs"]["weld_allowance"] == str(
        result.weld_allowance_path.resolve()
    )
    assert result.report["outputs"]["weld_allowance_report"] == str(
        result.weld_allowance_report_path.resolve()
    )
