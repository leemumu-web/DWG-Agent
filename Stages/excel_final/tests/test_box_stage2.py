from __future__ import annotations

from decimal import Decimal

import pytest

from canonical_pipeline import build_canonical_projection
from box_stage2 import (
    BoxDuplicatePartDrawingError,
    BoxMeasurementContractError,
    BoxSetbackMeasurement,
    build_box_plate_plans,
    enhance_box_projection,
    map_box_role,
    parse_box_measurement_contract,
)


class _NoHandbookLookup:
    def lookup(self, *_args, **_kwargs):
        raise AssertionError("BOX plate projection must not query the handbook")


def _contract_item(
    *,
    source_file_id: int,
    part_number: str,
    spec: str,
    status: str = "OK",
    measurements: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "source_file_id": source_file_id,
        "file_name": f"{part_number}_拆板前.dxf",
        "part_number": part_number,
        "classification_spec": spec,
        "reader_spec": spec,
        "status": status,
        "warnings": [],
        "measurements": measurements or [
            {"role": "翼", "left_safe": 0, "right_safe": 0},
            {"role": "腹", "left_safe": 0, "right_safe": 0},
        ],
    }


def test_box_measurement_contract_parses_versioned_fields() -> None:
    payload = {
        "schema": "box_setback_measurements/v1",
        "items": [_contract_item(source_file_id=7, part_number="w4e-cb-10", spec="BOX600*600*22*22")],
    }
    contract = parse_box_measurement_contract(payload)
    assert contract.schema == "box_setback_measurements/v1"
    assert len(contract.items) == 1
    assert contract.items[0].part_number == "w4e-cb-10"


def test_box_measurement_contract_rejects_wrong_schema() -> None:
    payload = {
        "schema": "bh_setback_measurements/v1",
        "items": [],
    }
    with pytest.raises(BoxMeasurementContractError):
        parse_box_measurement_contract(payload)


def test_box_measurement_contract_blocks_duplicate_part_drawings() -> None:
    payload = {
        "schema": "box_setback_measurements/v1",
        "items": [
            _contract_item(source_file_id=1, part_number="a1-1fd-cb-465", spec="BOX600*600*22*22"),
            _contract_item(source_file_id=2, part_number="a1-1fd-cb-465", spec="BOX600*600*22*22"),
        ],
    }
    with pytest.raises(BoxDuplicatePartDrawingError):
        parse_box_measurement_contract(payload)


def test_map_box_role_merged_and_split() -> None:
    assert map_box_role("a1", "翼").part_type == "BOX翼"
    assert map_box_role("a1", "翼").quantity_multiplier == Decimal("2")
    assert map_box_role("a1", "翼").import_part_no == "a1-BOX翼"
    assert map_box_role("a1", "腹").import_part_no == "a1-BOX腹"
    assert map_box_role("a1", "上腹").quantity_multiplier == Decimal("1")
    assert map_box_role("a1", "上腹").import_part_no == "a1-BOX上腹"
    assert map_box_role("a1", "下翼").import_part_no == "a1-BOX下翼"
    with pytest.raises(ValueError):
        map_box_role("a1", "未知角色")


def test_box_plate_plans_merged_form() -> None:
    plans = build_box_plate_plans(
        part_number="a1-1fd-cb-465",
        model_length=6745,
        material="Q355C",
        web_spec=22,
        web_width=556,
        flange_spec=22,
        flange_width=600,
        measurements=(
            BoxSetbackMeasurement("翼", Decimal("0"), Decimal("0")),
            BoxSetbackMeasurement("腹", Decimal("0"), Decimal("0")),
        ),
    )
    assert len(plans) == 2
    by_type = {plan.part_type: plan for plan in plans}
    assert by_type["BOX腹"].quantity_multiplier == Decimal("2")
    assert by_type["BOX翼"].quantity_multiplier == Decimal("2")
    assert by_type["BOX腹"].spec == Decimal("22")
    assert by_type["BOX腹"].width == Decimal("556")  # H - 2*tf
    assert by_type["BOX翼"].width == Decimal("600")  # W
    assert all(plan.cut_length == Decimal("6745") for plan in plans)


def test_box_plate_plans_split_form_with_setbacks() -> None:
    plans = build_box_plate_plans(
        part_number="w4e-cb-10",
        model_length=6745,
        material="Q355C",
        web_spec=22,
        web_width=556,
        flange_spec=22,
        flange_width=600,
        measurements=(
            BoxSetbackMeasurement("翼", Decimal("0"), Decimal("0")),
            BoxSetbackMeasurement("上腹", Decimal("20"), Decimal("0")),
            BoxSetbackMeasurement("下腹", Decimal("0"), Decimal("0")),
        ),
    )
    assert len(plans) == 3
    assert plans[0].import_part_no == "w4e-cb-10-BOX上腹"
    assert plans[0].left_safe == Decimal("20")
    assert plans[0].cut_length == Decimal("6725")
    assert plans[1].import_part_no == "w4e-cb-10-BOX下腹"
    assert plans[2].import_part_no == "w4e-cb-10-BOX翼"
    assert plans[2].quantity_multiplier == Decimal("2")


def test_box_plate_plans_rejects_incomplete_combination() -> None:
    with pytest.raises(ValueError):
        build_box_plate_plans(
            part_number="x",
            model_length=1000,
            material="Q355C",
            web_spec=22,
            web_width=556,
            flange_spec=22,
            flange_width=600,
            measurements=(
                BoxSetbackMeasurement("翼", Decimal("0"), Decimal("0")),
                BoxSetbackMeasurement("上腹", Decimal("20"), Decimal("0")),
                BoxSetbackMeasurement("翼", Decimal("0"), Decimal("0")),
            ),
        )


def test_box_plate_plans_rejects_nonpositive_cut_length() -> None:
    with pytest.raises(ValueError):
        build_box_plate_plans(
            part_number="x",
            model_length=100,
            material="Q355C",
            web_spec=22,
            web_width=556,
            flange_spec=22,
            flange_width=600,
            measurements=(
                BoxSetbackMeasurement("翼", Decimal("80"), Decimal("80")),
                BoxSetbackMeasurement("腹", Decimal("0"), Decimal("0")),
            ),
        )


def test_box_enhancement_fans_out_occurrences() -> None:
    parts = (
        _source_part(source_row=2, part_no="w4e-cb-10", spec="BOX600*600*22*22"),
    )
    projection = build_canonical_projection(
        parts=parts,
        component_rows=(),
        reader_issues=(),
        handbook=_NoHandbookLookup(),
    )
    contract = parse_box_measurement_contract({
        "schema": "box_setback_measurements/v1",
        "items": [_contract_item(
            source_file_id=1,
            part_number="w4e-cb-10",
            spec="BOX600*600*22*22",
            measurements=[
                {"role": "翼", "left_safe": 0, "right_safe": 0},
                {"role": "上腹", "left_safe": 20, "right_safe": 0},
                {"role": "下腹", "left_safe": 0, "right_safe": 0},
            ],
        )],
    })
    result = enhance_box_projection(projection, contract)
    assert result.status == "complete"
    assert result.matched_occurrence_count == 1
    organized = result.projection.organized_rows
    box_rows = [row for row in organized if str(row.get("类型") or "") in {"BOX腹", "BOX翼"}]
    assert len(box_rows) == 3
    upper = next(row for row in box_rows if row.get("导入零件号") == "w4e-cb-10-BOX上腹")
    assert upper["左进(mm)"] == Decimal("20")
    assert upper["下料长度(mm)"] == Decimal("6725")
    assert upper["_stage2_status"] == "complete"


def test_box_reader_failure_keeps_red_placeholders() -> None:
    parts = (
        _source_part(source_row=2, part_no="w4e-cb-10", spec="BOX600*600*22*22"),
    )
    projection = build_canonical_projection(
        parts=parts,
        component_rows=(),
        reader_issues=(),
        handbook=_NoHandbookLookup(),
    )
    contract = parse_box_measurement_contract({
        "schema": "box_setback_measurements/v1",
        "items": [_contract_item(
            source_file_id=1,
            part_number="w4e-cb-10",
            spec="BOX600*600*22*22",
            status="ERROR_CRANKED_UNSUPPORTED",
        )],
    })
    result = enhance_box_projection(projection, contract)
    assert result.status == "partial"
    assert result.manual_occurrence_count == 1
    box_rows = [
        row for row in result.projection.organized_rows
        if str(row.get("类型") or "") in {"BOX腹", "BOX翼"}
    ]
    assert box_rows and all(row.get("_stage2_status") == "manual" for row in box_rows)
    assert box_rows[0].get("下料长度(mm)") is None


def _source_part(*, source_row: int, part_no: str, spec: str):
    from decimal import Decimal as D

    from domain import SourcePart

    return SourcePart(
        source_sheet="原表",
        source_row=source_row,
        source_seq=source_row - 1,
        batch=None,
        component_no="G1",
        component_qty=D("1"),
        part_no=part_no,
        original_spec=spec,
        material="Q355C",
        length=D("6745"),
        original_qty=D("1"),
        source_unit_net=D("12"),
        source_total_net=D("12"),
        source_unit_gross=D("13"),
        source_total_gross=D("13"),
        source_unit_area=D("1.5"),
        source_total_area=D("1.5"),
        classification=None,
    )

