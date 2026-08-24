from __future__ import annotations

import json
import os
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import ezdxf
import pytest
from ezdxf import bbox
from steel_dxf_split.pl import split_pl
from steel_dxf_split.pl.development import _merge_collinear_lines
from steel_dxf_split.pl.geometry import _proved_components

COVERED_CENTER_OUTER_ONLY_PARTS = {
    "2b1-pb-77",
    "2b1-pb-79",
    "2b1-pb-101",
    "2b1-pb-133",
}


def _paired_directories() -> tuple[Path, Path]:
    source = os.environ.get("PL_PAIRED_SOURCE_DIR")
    reference = os.environ.get("PL_PAIRED_REFERENCE_DIR")
    if not source or not reference:
        pytest.skip("set PL_PAIRED_SOURCE_DIR and PL_PAIRED_REFERENCE_DIR")
    return Path(source), Path(reference)


def test_all_paired_pl_sources_match_the_professional_result_contract(
    tmp_path: Path,
) -> None:
    source_dir, reference_dir = _paired_directories()
    source_names = {path.name for path in source_dir.glob("*.dxf")}
    reference_names = {path.name for path in reference_dir.glob("*.dxf")}
    assert source_names
    assert source_names == reference_names

    batch = split_pl(source_dir, tmp_path / "results")
    report = json.loads(batch.report_path.read_text(encoding="utf-8"))
    assert batch.success_count == len(source_names)
    assert batch.rejected_count == 0
    items_by_part = {item["part_number"]: item for item in report["items"]}
    assert tuple(items_by_part["2b1-cb-61"]["transform"]["carrier_interval_indices"]) == (1,)
    assert tuple(items_by_part["2b1-cb-62"]["transform"]["carrier_interval_indices"]) == (1,)

    for item in report["items"]:
        raw = Decimal(str(item["lengths"]["raw_mm"]))
        target = Decimal(str(item["lengths"]["target_mm"]))
        assert target == raw.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
        assert target >= raw

        output = ezdxf.readfile(item["output"]["path"])
        output_cut = tuple(
            entity for entity in output.modelspace() if entity.dxf.layer == "PLATE_CUT"
        )
        labels = tuple(output.modelspace().query('TEXT[layer=="PART_LABEL"]'))
        assert len(labels) == 1
        assert labels[0].dxf.text == f"p={item['part_number']}"
        assert 10.0 <= float(labels[0].dxf.height) <= 30.0

        reference = ezdxf.readfile(reference_dir / f"{item['part_number']}.dxf")
        reference_loops = tuple(
            entity
            for entity in reference.modelspace()
            if entity.dxftype() in {"LWPOLYLINE", "POLYLINE"}
        )
        output_components = _proved_components(output_cut)
        assert len(output_components) >= len(reference_loops)
        if item["part_number"] in COVERED_CENTER_OUTER_ONLY_PARTS:
            assert len(output_components) == 4

        output_main = max(output_components, key=lambda component: component.polygon.area)
        output_main_entities = tuple(segment.entity for segment in output_main.segments)
        assert not any(
            _merge_collinear_lines(first, second) is not None
            for index, first in enumerate(output_main_entities)
            for second in output_main_entities[index + 1 :]
        )
        reference_main = max(
            reference_loops,
            key=lambda entity: (
                bbox.extents((entity,), fast=False).size.x
                * bbox.extents((entity,), fast=False).size.y
            ),
        )
        reference_bounds = bbox.extents((reference_main,), fast=False)
        output_dimensions = sorted(
            (
                output_main.polygon.bounds[2] - output_main.polygon.bounds[0],
                output_main.polygon.bounds[3] - output_main.polygon.bounds[1],
            )
        )
        reference_dimensions = sorted((reference_bounds.size.x, reference_bounds.size.y))
        assert output_dimensions == pytest.approx(reference_dimensions, abs=2.1)
