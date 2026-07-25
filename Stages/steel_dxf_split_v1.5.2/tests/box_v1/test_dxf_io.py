from __future__ import annotations

from pathlib import Path

import pytest

from steel_dxf_split.box.dxf_io import (
    DXFLoadError,
    decode_cad_text_transport,
    iter_modelspace_entities,
    load_document,
    normalize_text,
)
from tests.box_v1.paths import INPUTS

SAMPLE = INPUTS / "2b1-cb-56_拆板前.dxf"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"16\M+5A6B522", "16Φ22"),
        (r"\U+4E2D\U+6587", "中文"),
        ("%%c22", "Φ22"),
        ("%%C22", "Φ22"),
        ("¦µ22", "Φ22"),
        (r"上翼\PBOX", r"上翼\PBOX"),
    ],
)
def test_decode_cad_transport_before_semantic_interpretation(
    raw: str,
    expected: str,
) -> None:
    assert decode_cad_text_transport(raw) == expected


def test_normalize_text_removes_layout_only_after_transport_decode() -> None:
    assert normalize_text(r"{\H0.7x;16\M+5A6B522}\P孔") == "16Φ22 孔"


def test_load_document_audits_real_tekla_export() -> None:
    document = load_document(SAMPLE)

    assert document.dxfversion == "AC1032"
    assert document.header["$INSUNITS"] == 4
    assert document.header["$DWGCODEPAGE"] == "GB2312"
    assert not document.audit().has_errors


def test_iter_modelspace_entities_expands_tekla_object_groups() -> None:
    document = load_document(SAMPLE)
    expanded = tuple(iter_modelspace_entities(document))

    assert expanded
    assert all(entity.dxftype() != "INSERT" for entity in expanded)
    assert sum(entity.dxf.layer == "Part" for entity in expanded) == 18


def test_dwg_is_rejected_with_actionable_error(tmp_path: Path) -> None:
    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"not a real dwg")

    with pytest.raises(DXFLoadError, match="convert it to DXF"):
        load_document(source)
