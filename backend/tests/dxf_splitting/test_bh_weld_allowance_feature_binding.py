from __future__ import annotations

from dataclasses import replace
from functools import cache
from pathlib import Path

import ezdxf
import pytest
from steel_dxf_split.bh_associations import DrawingEdgeKind
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import ManufacturingPlateRole
from steel_dxf_split.bh_writer import OutputPurpose, write_bh_clean
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.paired_output import (
    PairedOutputValidationError,
    validate_paired_outputs,
)
from steel_dxf_split.weld_allowance import (
    _validate_and_transform,
    apply_weld_allowance,
)

from tests.support.sample_roots import b4_sample_root, require_sample

_SAMPLE_ROOT = b4_sample_root()
_XDATA_APPID = "STEEL_DXF_SPLIT"
_CUT_XDATA_SCHEMA = "BH-CUT-FEATURE-1.0"


@cache
def _compile(sample: str):
    source = _SAMPLE_ROOT / f"{sample}.dxf"
    return compile_bh_document(
        load_document(require_sample(source)),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )


def _datum_cut_source_ids(compiled, end_role: str) -> set[str]:
    nodes = {node.node_id: node for node in compiled.drawing_graph.nodes}
    web_region_id = compiled.assembly.web_plate.provenance["source_region_id"]
    return {
        source_id
        for edge in compiled.drawing_graph.edges_of(DrawingEdgeKind.ALIGNED_WITH)
        if edge.rule_id == "TEKLA.DIMENSION.END_DATUM_CUT"
        if edge.attributes.get("end_role") == end_role
        if edge.attributes.get("region_id") == web_region_id
        for source_id in nodes[edge.target].source_ids
    }


@pytest.mark.parametrize(
    ("sample", "positive_count", "negative_count"),
    [
        ("b4-3-cb-12", 5, 0),
        ("b4-3-cb-13", 5, 0),
        ("b4-3-cb-17", 2, 2),
        ("b4-3-cb-18", 2, 2),
    ],
)
def test_exploded_end_dimensions_bind_only_their_witnessed_cut_columns(
    sample: str,
    positive_count: int,
    negative_count: int,
) -> None:
    """Dimension witnesses, not nearest-end distance, assign the cut datum."""

    compiled = _compile(sample)

    positive = _datum_cut_source_ids(compiled, "positive_x")
    negative = _datum_cut_source_ids(compiled, "negative_x")

    assert len(positive) == positive_count
    assert len(negative) == negative_count
    assert positive.isdisjoint(negative)


@pytest.mark.parametrize(
    ("sample", "moving_count"),
    [
        ("b4-3-cb-12", 5),
        ("b4-3-cb-13", 5),
        ("b4-3-cb-17", 2),
        ("b4-3-cb-18", 2),
    ],
)
def test_manufacturing_contract_freezes_positive_terminal_cut_ids(
    sample: str,
    moving_count: int,
) -> None:
    compiled = _compile(sample)
    web = next(
        plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role == ManufacturingPlateRole.WEB
    )
    contract = web.weld_allowance_contract

    assert contract is not None
    assert len(contract.positive_terminal_cut_ids) == moving_count
    moving = set(contract.positive_terminal_cut_ids)
    assert moving <= {cut.cut_id for cut in web.circular_cuts}


def test_manufacturing_validator_rejects_an_unknown_allowance_cut_id() -> None:
    from steel_dxf_split.bh_validator import validate_bh_manufacturing_ir

    compiled = _compile("b4-3-cb-17")
    manufacturing = compiled.manufacturing_ir
    web_index = next(
        index
        for index, plate in enumerate(manufacturing.plates)
        if plate.role == ManufacturingPlateRole.WEB
    )
    plates = list(manufacturing.plates)
    contract = plates[web_index].weld_allowance_contract
    assert contract is not None
    plates[web_index] = replace(
        plates[web_index],
        weld_allowance_contract=replace(
            contract,
            positive_terminal_cut_ids=("unknown-cut",),
        ),
    )

    validation = validate_bh_manufacturing_ir(
        replace(manufacturing, plates=tuple(plates)),
        compiled.assembly,
    )

    assert validation.ok is False
    assert validation.checks["weld_allowance_contracts_match_geometry"] is False


def _bound_circles(document: ezdxf.document.Drawing) -> dict[str, object]:
    result = {}
    for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']"):
        tags = list(entity.get_xdata(_XDATA_APPID))
        assert [tag.code for tag in tags] == [1000, 1000, 1000]
        assert tags[0].value == _CUT_XDATA_SCHEMA
        cut_id = str(tags[2].value)
        assert cut_id not in result
        result[cut_id] = entity
    return result


@pytest.mark.parametrize("sample", ["b4-3-cb-12", "b4-3-cb-13", "b4-3-cb-17", "b4-3-cb-18"])
def test_allowance_moves_only_contract_bound_positive_terminal_cuts(
    sample: str,
    tmp_path: Path,
) -> None:
    compiled = _compile(sample)
    normal_path = tmp_path / f"{sample}.dxf"
    write_bh_clean(
        compiled.manufacturing_ir,
        normal_path,
        purpose=OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(normal_path)
    before = {
        cut_id: tuple(float(value) for value in entity.dxf.center)
        for cut_id, entity in _bound_circles(document).items()
    }

    manufacturing_payload = compiled.manufacturing_ir.to_dict()
    manufacturing_payload["fingerprint"] = compiled.manufacturing_ir.fingerprint
    plate_results = _validate_and_transform(
        document,
        {"manufacturing_ir": manufacturing_payload},
    )
    after = {
        cut_id: tuple(float(value) for value in entity.dxf.center)
        for cut_id, entity in _bound_circles(document).items()
    }
    contracts = {
        plate.plate_id: plate.weld_allowance_contract
        for plate in compiled.manufacturing_ir.plates
    }
    expected_moving = {
        cut_id: contract.allowance_mm
        for contract in contracts.values()
        if contract is not None
        for cut_id in contract.positive_terminal_cut_ids
    }

    assert expected_moving
    assert {item["plate_id"] for item in plate_results} <= set(contracts)
    assert {item["plate_id"] for item in plate_results} == {
        str(entity.get_xdata(_XDATA_APPID)[1].value)
        for entity in document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    }
    for cut_id, center in before.items():
        assert after[cut_id][0] - center[0] == pytest.approx(
            expected_moving.get(cut_id, 0.0)
        )
        assert after[cut_id][1] == pytest.approx(center[1])


def _compilation_report(compiled, normal_path: Path) -> dict[str, object]:
    manufacturing = compiled.manufacturing_ir.to_dict()
    manufacturing["fingerprint"] = compiled.manufacturing_ir.fingerprint
    return {
        "version": "1.5.2",
        "report_schema": "BH-COMPILATION-REPORT-1.4",
        "automation_route": "production",
        "saved_dxf": {"ok": True},
        "manufacturing_ir_validation": {"ok": True},
        "manufacturing_ir": manufacturing,
        "outputs": {"production_clean": str(normal_path.resolve())},
    }


def test_saved_allowance_and_pair_validator_accept_only_declared_cut_motion(
    tmp_path: Path,
) -> None:
    compiled = _compile("b4-3-cb-17")
    normal_path = tmp_path / "normal.dxf"
    allowance_path = tmp_path / "allowance.dxf"
    compilation_report_path = tmp_path / "compilation.json"
    allowance_report_path = tmp_path / "allowance.json"
    write_bh_clean(
        compiled.manufacturing_ir,
        normal_path,
        purpose=OutputPurpose.PRODUCTION,
    )
    compilation_report_path.write_text(
        __import__("json").dumps(
            _compilation_report(compiled, normal_path),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    apply_weld_allowance(
        normal_path,
        compilation_report_path,
        allowance_path,
        allowance_report_path,
    )
    validation = validate_paired_outputs(
        normal_path,
        allowance_path,
        allowance_report_path,
        family="BH",
    )

    assert validation["ok"] is True
    assert validation["checks"]["cut_hole_feature_contracts_match"] is True


def test_pair_validator_rejects_an_undeclared_cut_translation(tmp_path: Path) -> None:
    compiled = _compile("b4-3-cb-17")
    normal_path = tmp_path / "normal.dxf"
    allowance_path = tmp_path / "allowance.dxf"
    compilation_report_path = tmp_path / "compilation.json"
    allowance_report_path = tmp_path / "allowance.json"
    write_bh_clean(
        compiled.manufacturing_ir,
        normal_path,
        purpose=OutputPurpose.PRODUCTION,
    )
    compilation_report_path.write_text(
        __import__("json").dumps(
            _compilation_report(compiled, normal_path),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    apply_weld_allowance(
        normal_path,
        compilation_report_path,
        allowance_path,
        allowance_report_path,
    )
    document = ezdxf.readfile(allowance_path)
    declared = {
        cut_id
        for plate in compiled.manufacturing_ir.plates
        if plate.weld_allowance_contract is not None
        for cut_id in plate.weld_allowance_contract.positive_terminal_cut_ids
    }
    stationary = next(
        entity
        for cut_id, entity in _bound_circles(document).items()
        if cut_id not in declared
    )
    stationary.dxf.center = (
        float(stationary.dxf.center.x) + 1.0,
        float(stationary.dxf.center.y),
        float(stationary.dxf.center.z),
    )
    document.saveas(allowance_path)

    with pytest.raises(
        PairedOutputValidationError,
        match="feature contract",
    ):
        validate_paired_outputs(
            normal_path,
            allowance_path,
            allowance_report_path,
            family="BH",
        )
