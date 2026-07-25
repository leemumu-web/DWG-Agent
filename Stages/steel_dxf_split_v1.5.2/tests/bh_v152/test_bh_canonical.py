from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.bh_canonical import (
    UnsupportedDrawingUnits,
    canonical_sha256,
    canonical_source_payload,
    resolve_units,
)
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_source import decode_source_document
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _line_doc(*, units: int, end_x: float, reverse: bool = False, layer: str = "Part"):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    if not doc.layers.has_entry(layer):
        doc.layers.add(layer)
    start, end = ((end_x, 0), (0, 0)) if reverse else ((0, 0), (end_x, 0))
    doc.modelspace().add_line(start, end, dxfattribs={"layer": layer})
    return doc


def _source_hash(doc) -> str:
    source = decode_source_document(doc)
    return canonical_sha256(canonical_source_payload(source, grid_mm=1e-6))


def test_equivalent_millimetre_and_metre_geometry_has_same_payload() -> None:
    millimetres = decode_source_document(_line_doc(units=4, end_x=1000.0))
    metres = decode_source_document(_line_doc(units=6, end_x=1.0))

    assert canonical_source_payload(millimetres, grid_mm=1e-6) == canonical_source_payload(
        metres,
        grid_mm=1e-6,
    )


def test_line_endpoint_order_does_not_change_source_fingerprint() -> None:
    forward = _line_doc(units=4, end_x=1000.0)
    reversed_line = _line_doc(units=4, end_x=1000.0, reverse=True)
    assert _source_hash(forward) == _source_hash(reversed_line)


def test_closed_polyline_start_and_winding_do_not_change_source_fingerprint() -> None:
    points = [(0, 0), (100, 0), (100, 50), (0, 50)]
    variants = (
        points,
        points[2:] + points[:2],
        list(reversed(points)),
    )
    hashes = []
    for vertices in variants:
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4
        doc.layers.add("Part")
        doc.modelspace().add_lwpolyline(
            vertices,
            close=True,
            dxfattribs={"layer": "Part"},
        )
        hashes.append(_source_hash(doc))
    assert len(set(hashes)) == 1


def test_entity_database_order_does_not_change_source_fingerprint() -> None:
    def drawing(order: tuple[int, int]):
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4
        doc.layers.add("Part")
        lines = (
            ((0, 0), (100, 0)),
            ((0, 50), (100, 50)),
        )
        for index in order:
            doc.modelspace().add_line(*lines[index], dxfattribs={"layer": "Part"})
        return doc

    assert _source_hash(drawing((0, 1))) == _source_hash(drawing((1, 0)))


def test_geometry_and_semantic_role_mutations_change_source_fingerprint() -> None:
    base = _line_doc(units=4, end_x=1000.0)
    moved = _line_doc(units=4, end_x=1000.001)
    unknown_role = _line_doc(units=4, end_x=1000.0, layer="CUSTOM")

    assert _source_hash(base) != _source_hash(moved)
    assert _source_hash(base) != _source_hash(unknown_role)


def test_unknown_units_are_explicitly_invalid() -> None:
    resolution = resolve_units(0)
    assert not resolution.valid
    assert resolution.scale_to_mm is None
    with pytest.raises(UnsupportedDrawingUnits, match="INSUNITS=0"):
        canonical_source_payload(
            decode_source_document(_line_doc(units=0, end_x=1.0)),
            grid_mm=1e-6,
        )


def test_compiler_uses_complete_canonical_source_fingerprint_v2() -> None:
    source = PAIR_DIR / "3b2-cb-86_拆板前.dxf"
    first = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    second = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    assert first.fingerprints["algorithm"] == "sha256-canonical-json-v3"
    assert first.fingerprints == second.fingerprints
    assert len(first.fingerprints["source_fact_ir"]) == 64
