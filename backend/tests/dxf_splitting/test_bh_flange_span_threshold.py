from __future__ import annotations

import os
from pathlib import Path

import pytest
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_extractor import _material_flange_span_delta
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.dxf_io import load_document

_ROOT = Path(
    os.environ.get(
        "BH_A1_SAMPLE_ROOT",
        "/home/Creeken/Downloads/原DXF",
    )
)


def _source(number: int) -> Path:
    matches = tuple(_ROOT.glob(f"*cb-{number}_拆板前.dxf"))
    if len(matches) != 1:
        pytest.skip(f"A1 DXF sample is unavailable or ambiguous: cb-{number}")
    return matches[0]


def _compile(number: int):
    source = _source(number)
    return compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )


def _flange_lengths(compiled) -> list[float]:
    return sorted(
        max(plate.bbox.width, plate.bbox.height)
        for plate in compiled.assembly.flange_plates
        for _ in range(plate.quantity)
    )


def test_source_pair_delta_uses_absolute_geometry_tolerance() -> None:
    assert _material_flange_span_delta(7790.0, 7766.0, 0.15) is True
    assert _material_flange_span_delta(7790.0, 7770.0, 0.15) is True
    assert _material_flange_span_delta(7790.0, 7789.4, 0.15) is False


def test_cb183_does_not_merge_two_source_backed_flange_lengths() -> None:
    compiled = _compile(183)

    assert _flange_lengths(compiled) == pytest.approx([7766.0, 7790.0], abs=0.2)
    assert all(plate.quantity == 1 for plate in compiled.assembly.flange_plates)


def test_cb195_does_not_merge_two_source_backed_flange_lengths() -> None:
    compiled = _compile(195)

    assert _flange_lengths(compiled) == pytest.approx([7770.0, 7790.0], abs=0.2)
    assert all(plate.quantity == 1 for plate in compiled.assembly.flange_plates)


def test_cb117_projection_artifact_does_not_create_a_short_flat_flange() -> None:
    compiled = _compile(117)

    assert len(compiled.assembly.flange_plates) == 1
    assert compiled.assembly.flange_plates[0].quantity == 2
    assert _flange_lengths(compiled) == pytest.approx([7600.0, 7600.0], abs=0.2)
