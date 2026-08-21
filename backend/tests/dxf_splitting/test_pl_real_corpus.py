from __future__ import annotations

import json
import os
from pathlib import Path

import ezdxf
import pytest
from steel_dxf_split.pl import split_pl

EXPECTED_CARRIER_POSITION = {
    "q4-b-181": "middle",
    "q6-b-62": "only",
    "q6-b-71": "last",
    "q6-cb-21": "only",
    "q7-b-21": "middle",
    "q7-b-404": "middle",
    "q7-b-446": "only",
    "q7-b-623": "middle",
    "q7-b-628": "middle",
    "z2-cb-104": "last",
    "z2-cb-207": "last",
    "z2-cb-209": "first",
    "z2-cb-230": "first",
    "z2-cb-231": "only",
    "z2-cb-338": "only",
    "z2-cb-347": "first",
    "z2-cb-348": "first",
    "z2-cb-350": "first",
    "z2-cb-78": "last",
    "z2-cb-79": "last",
    "z4-cb-17": "first",
}


def _carrier_position(interval_count: int, indices: tuple[int, ...]) -> str:
    if interval_count == 1:
        return "only"
    if min(indices) == 0:
        return "first"
    if max(indices) == interval_count - 1:
        return "last"
    return "middle"


def _real_corpus_paths() -> tuple[Path, Path]:
    source_value = os.environ.get("PL_REAL_SOURCE_DXF")
    reference_value = os.environ.get("PL_REAL_REFERENCE_DIR")
    if not source_value or not reference_value:
        pytest.skip("set PL_REAL_SOURCE_DXF and PL_REAL_REFERENCE_DIR to run the 21-part PL corpus")
    return Path(source_value), Path(reference_value)


def test_all_21_real_parts_share_the_carrier_unfolding_contract(tmp_path: Path) -> None:
    source_dxf, reference_dir = _real_corpus_paths()
    expected_names = set(EXPECTED_CARRIER_POSITION)
    missing_references = sorted(
        part_number
        for part_number in expected_names
        if not (reference_dir / f"{part_number}.dxf").is_file()
    )
    assert not missing_references

    batch = split_pl(source_dxf, tmp_path / "results")
    report = json.loads(batch.report_path.read_text(encoding="utf-8"))

    assert batch.success_count == 21
    assert batch.rejected_count == 0
    assert report["schema"] == "steel-dxf-split-pl-report/2"
    assert report["success_count"] == 21
    assert report["rejected_count"] == 0
    assert {item["part_number"] for item in report["items"]} == expected_names
    assert {path.stem for path in batch.output_dir.glob("*.dxf")} == expected_names

    items_by_part = {item["part_number"]: item for item in report["items"]}
    for part_number, expected_position in EXPECTED_CARRIER_POSITION.items():
        item = items_by_part[part_number]
        lengths = item["lengths"]
        target = lengths["target_mm"]
        raw = max(lengths["projection_mm"], lengths["k_length_mm"], lengths["bom_mm"])
        assert target >= lengths["projection_mm"]
        assert target >= lengths["k_length_mm"]
        assert target >= lengths["bom_mm"]
        assert 0 <= target - raw < 0.1

        transform = item["transform"]
        intervals = transform["intervals"]
        carrier_indices = tuple(transform["carrier_interval_indices"])
        assert carrier_indices == tuple(range(carrier_indices[0], carrier_indices[-1] + 1))
        assert (
            tuple(interval["index"] for interval in intervals if interval["is_carrier"])
            == carrier_indices
        )
        assert _carrier_position(len(intervals), carrier_indices) == expected_position

        upper_growth = 0.0
        lower_growth = 0.0
        for interval in intervals:
            upper_delta = interval["output_upper_span_mm"] - interval["source_upper_span_mm"]
            lower_delta = interval["output_lower_span_mm"] - interval["source_lower_span_mm"]
            if interval["is_carrier"]:
                assert upper_delta >= -0.001
                assert lower_delta >= -0.001
                upper_growth += upper_delta
                lower_growth += lower_delta
            else:
                assert abs(upper_delta) <= 0.001
                assert abs(lower_delta) <= 0.001
        assert upper_growth == pytest.approx(lengths["total_extension_mm"], abs=0.001)
        assert lower_growth == pytest.approx(lengths["total_extension_mm"], abs=0.001)

        output = item["output"]
        assert output["label"] == f"p={part_number}"
        assert output["width_mm"] == pytest.approx(item["geometry"]["source_width_mm"], abs=0.001)
        saved = ezdxf.readfile(output["path"])
        labels = tuple(entity for entity in saved.modelspace() if entity.dxf.layer == "PART_LABEL")
        assert len(labels) == 1
        assert labels[0].dxf.text == f"p={part_number}"
        assert labels[0].dxf.height == pytest.approx(30.0)
        assert saved.audit().has_errors is False

    q7_lengths = items_by_part["q7-b-404"]["lengths"]
    assert q7_lengths["target_mm"] == pytest.approx(1162.2)
    assert q7_lengths["total_extension_mm"] == pytest.approx(8.134386, abs=0.000001)
    assert q7_lengths["total_extension_mm"] != pytest.approx(10.2)
