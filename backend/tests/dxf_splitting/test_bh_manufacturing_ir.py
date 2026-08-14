from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import ManufacturingPlateRole
from steel_dxf_split.bh_validator import validate_bh_manufacturing_ir
from steel_dxf_split.dxf_io import load_document


_SAMPLE_ROOT = Path(
    r"D:\Documents\Codex\DWG-Agent拆板问题样本\最终交付\01_全部原文件"
)


@pytest.fixture(scope="module")
def cb2_compiled():
    source = _SAMPLE_ROOT / "b4-3-cb-2.dxf"
    return compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )


def test_final_flange_roles_generate_their_canonical_labels(cb2_compiled) -> None:
    """Final upper/lower roles must not retain the extractor's opposite labels."""

    labels = {
        plate.role: plate.label
        for plate in cb2_compiled.manufacturing_ir.plates
    }

    assert labels[ManufacturingPlateRole.UPPER_FLANGE] == "p=b4-3-cb-2上翼"
    assert labels[ManufacturingPlateRole.LOWER_FLANGE] == "p=b4-3-cb-2下翼"


def test_manufacturing_validator_rejects_a_role_label_contradiction(
    cb2_compiled,
) -> None:
    """A future label copy regression must block production routing."""

    manufacturing = cb2_compiled.manufacturing_ir
    upper_index = next(
        index
        for index, plate in enumerate(manufacturing.plates)
        if plate.role == ManufacturingPlateRole.UPPER_FLANGE
    )
    plates = list(manufacturing.plates)
    plates[upper_index] = replace(
        plates[upper_index],
        label="p=b4-3-cb-2下翼",
    )
    contradictory = replace(manufacturing, plates=tuple(plates))

    validation = validate_bh_manufacturing_ir(
        contradictory,
        cb2_compiled.assembly,
    )

    assert validation.ok is False
    assert validation.checks["final_role_labels_match"] is False
