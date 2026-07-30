from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from canonical_pipeline import build_canonical_projection
from domain import SourcePart
from part_builder import build_part_rows
from quality import QualityLedger
from bh_stage2 import (
    BhDuplicatePartDrawingError,
    BhMeasurementContractError,
    BhSetbackMeasurement,
    build_bh_plate_plans,
    enhance_bh_projection,
    map_bh_role,
    parse_bh_measurement_contract,
)


class _NoHandbookLookup:
    def lookup(self, *_args, **_kwargs):
        raise AssertionError("BH plate projection must not query the handbook")


def _source_part(
    *,
    source_row: int,
    component_no: str,
    component_qty: str,
    part_no: str = "P-1",
    length: str = "1000",
    original_qty: str = "2",
    original_spec: str = "BH500*300*10*16",
) -> SourcePart:
    return SourcePart(
        source_sheet="原表",
        source_row=source_row,
        source_seq=source_row - 1,
        batch=None,
        component_no=component_no,
        component_qty=Decimal(component_qty),
        part_no=part_no,
        original_spec=original_spec,
        material="Q355B",
        length=Decimal(length),
        original_qty=Decimal(original_qty),
        source_unit_net=Decimal("12"),
        source_total_net=Decimal("24"),
        source_unit_gross=Decimal("13"),
        source_total_gross=Decimal("26"),
        source_unit_area=Decimal("1.5"),
        source_total_area=Decimal("3"),
        classification=None,
    )


def _projection(*parts: SourcePart):
    return build_canonical_projection(
        parts=parts,
        component_rows=(),
        reader_issues=(),
        handbook=_NoHandbookLookup(),
    )


def _contract_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "source_file_id": 101,
        "file_name": "P-1_拆板前.dxf",
        "part_number": "P-1",
        "classification_spec": "BH500*300*10*16",
        "reader_spec": "BH500*300*10*16",
        "status": "OK",
        "warnings": ["单位已核验"],
        "measurements": [
            {"role": "腹", "left_safe": 10, "right_safe": 20},
            {"role": "翼", "left_safe": 30, "right_safe": 40},
        ],
    }
    item.update(overrides)
    return item


def test_measurement_contract_parses_only_the_versioned_compact_fields() -> None:
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item()],
    })

    assert contract.schema == "bh_setback_measurements/v1"
    assert len(contract.items) == 1
    drawing = contract.items[0]
    assert drawing.source_file_id == 101
    assert drawing.file_name == "P-1_拆板前.dxf"
    assert drawing.part_number == "P-1"
    assert drawing.classification_spec == "BH500*300*10*16"
    assert drawing.reader_spec == "BH500*300*10*16"
    assert drawing.status == "OK"
    assert drawing.warnings == ("单位已核验",)
    assert [
        (measurement.role, measurement.left_safe, measurement.right_safe)
        for measurement in drawing.measurements
    ] == [
        ("腹", Decimal("10"), Decimal("20")),
        ("翼", Decimal("30"), Decimal("40")),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "bh_setback_measurements/v2", "items": []},
        {"schema": "bh_setback_measurements/v1"},
        {"schema": "bh_setback_measurements/v1", "items": "not-a-list"},
        {
            "schema": "bh_setback_measurements/v1",
            "items": [_contract_item(unexpected="value")],
        },
        {
            "schema": "bh_setback_measurements/v1",
            "items": [_contract_item(source_file_id=True)],
        },
        {
            "schema": "bh_setback_measurements/v1",
            "items": [_contract_item(measurements=[{"role": "腹", "left_safe": 1}])],
        },
    ],
)
def test_measurement_contract_rejects_schema_or_field_drift(
    payload: object,
) -> None:
    with pytest.raises(BhMeasurementContractError):
        parse_bh_measurement_contract(payload)


def test_measurement_contract_blocks_duplicate_normalized_part_drawings() -> None:
    first = _contract_item(
        source_file_id=101,
        file_name="first.dxf",
        part_number=" Ｐ-１ ",
    )
    second = _contract_item(
        source_file_id=102,
        file_name="second.dxf",
        part_number="p-1",
    )

    with pytest.raises(BhDuplicatePartDrawingError) as caught:
        parse_bh_measurement_contract({
            "schema": "bh_setback_measurements/v1",
            "items": [first, second],
        })

    assert caught.value.code == "EXCEL_STAGE2_DUPLICATE_PART_DRAWING"
    assert caught.value.conflicts == {"p-1": ("first.dxf", "second.dxf")}


def test_duplicate_detection_has_no_cross_project_or_cross_call_state() -> None:
    payload = {
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item(part_number="P-1")],
    }

    assert len(parse_bh_measurement_contract(payload).items) == 1
    assert len(parse_bh_measurement_contract(payload).items) == 1


def test_measurement_contract_rejects_reused_source_file_identity() -> None:
    with pytest.raises(BhMeasurementContractError, match="source_file_id"):
        parse_bh_measurement_contract({
            "schema": "bh_setback_measurements/v1",
            "items": [
                _contract_item(source_file_id=101, part_number="P-1"),
                _contract_item(source_file_id=101, part_number="P-2"),
            ],
        })


def test_identical_wings_merge_but_ordered_setbacks_do_not() -> None:
    shared = {
        "part_number": "P-1",
        "model_length": Decimal("1000"),
        "material": "Q355B",
        "web_spec": Decimal("10"),
        "web_width": Decimal("468"),
        "flange_spec": Decimal("16"),
        "flange_width": Decimal("300"),
    }
    merged = build_bh_plate_plans(
        measurements=(
            BhSetbackMeasurement("腹", Decimal("10"), Decimal("20")),
            BhSetbackMeasurement("上翼", Decimal("100"), Decimal("200")),
            BhSetbackMeasurement("下翼", Decimal("100"), Decimal("200")),
        ),
        **shared,
    )

    assert [plate.import_part_no for plate in merged] == ["P-1-BH腹", "P-1-BH翼"]
    assert merged[0].source_roles == ("腹",)
    assert merged[0].cut_length == Decimal("970")
    assert merged[1].source_roles == ("上翼", "下翼")
    assert merged[1].quantity_multiplier == Decimal("2")
    assert merged[1].cut_length == Decimal("700")

    ordered = build_bh_plate_plans(
        measurements=(
            BhSetbackMeasurement("腹", Decimal("10"), Decimal("20")),
            BhSetbackMeasurement("上翼", Decimal("100"), Decimal("200")),
            BhSetbackMeasurement("下翼", Decimal("200"), Decimal("100")),
        ),
        **shared,
    )

    assert [plate.import_part_no for plate in ordered] == [
        "P-1-BH腹",
        "P-1-BH上翼",
        "P-1-BH下翼",
    ]
    assert [
        (plate.left_safe, plate.right_safe) for plate in ordered[1:]
    ] == [
        (Decimal("100"), Decimal("200")),
        (Decimal("200"), Decimal("100")),
    ]


@pytest.mark.parametrize(
    "roles",
    [
        ("腹", "上翼"),
        ("腹", "翼", "上翼", "下翼"),
        ("腹", "翼-1", "翼-3"),
        ("腹", "上翼-1", "下翼-2"),
    ],
)
def test_incomplete_or_mixed_role_combinations_are_rejected(
    roles: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="角色组合不完整"):
        build_bh_plate_plans(
            part_number="P-1",
            model_length=Decimal("1000"),
            material="Q355B",
            web_spec=Decimal("10"),
            web_width=Decimal("468"),
            flange_spec=Decimal("16"),
            flange_width=Decimal("300"),
            measurements=tuple(
                BhSetbackMeasurement(role, Decimal("0"), Decimal("0"))
                for role in roles
            ),
        )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Decimal("-1"), Decimal("0")),
        (Decimal("NaN"), Decimal("0")),
        (Decimal("600"), Decimal("400")),
    ],
)
def test_invalid_setbacks_or_nonpositive_cut_length_are_rejected(
    left: Decimal,
    right: Decimal,
) -> None:
    with pytest.raises(ValueError):
        build_bh_plate_plans(
            part_number="P-1",
            model_length=Decimal("1000"),
            material="Q355B",
            web_spec=Decimal("10"),
            web_width=Decimal("468"),
            flange_spec=Decimal("16"),
            flange_width=Decimal("300"),
            measurements=(
                BhSetbackMeasurement("腹", left, right),
                BhSetbackMeasurement("翼", Decimal("0"), Decimal("0")),
            ),
        )


@pytest.mark.parametrize(
    ("measurements", "expected_ids", "expected_roles"),
    [
        (
            (
                BhSetbackMeasurement("翼-2", Decimal("700"), Decimal("0")),
                BhSetbackMeasurement("腹", Decimal("0"), Decimal("0")),
                BhSetbackMeasurement("翼-1", Decimal("0"), Decimal("700")),
            ),
            ("P-1-BH腹", "P-1-BH翼-1", "P-1-BH翼-2"),
            (("腹",), ("翼-1",), ("翼-2",)),
        ),
        (
            (
                BhSetbackMeasurement("下翼-2", Decimal("700"), Decimal("0")),
                BhSetbackMeasurement("上翼-1", Decimal("0"), Decimal("700")),
                BhSetbackMeasurement("腹", Decimal("0"), Decimal("0")),
                BhSetbackMeasurement("下翼-1", Decimal("0"), Decimal("700")),
                BhSetbackMeasurement("上翼-2", Decimal("700"), Decimal("0")),
            ),
            ("P-1-BH腹", "P-1-BH翼-1", "P-1-BH翼-2"),
            (("腹",), ("上翼-1", "下翼-1"), ("上翼-2", "下翼-2")),
        ),
    ],
)
def test_numbered_wing_roles_expand_in_stable_physical_order(
    measurements: tuple[BhSetbackMeasurement, ...],
    expected_ids: tuple[str, ...],
    expected_roles: tuple[tuple[str, ...], ...],
) -> None:
    plans = build_bh_plate_plans(
        part_number="P-1",
        model_length=Decimal("1000"),
        material="Q355B",
        web_spec=Decimal("10"),
        web_width=Decimal("468"),
        flange_spec=Decimal("16"),
        flange_width=Decimal("300"),
        measurements=measurements,
    )

    assert tuple(plan.import_part_no for plan in plans) == expected_ids
    assert tuple(plan.source_roles for plan in plans) == expected_roles
    assert all(plan.quantity_multiplier == Decimal("2") for plan in plans[1:])


def test_one_reader_drawing_fans_out_to_each_excel_source_occurrence() -> None:
    projection = _projection(
        _source_part(source_row=8, component_no="C1", component_qty="2", length="1000"),
        _source_part(source_row=9, component_no="C2", component_qty="3", length="1200"),
    )
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item(measurements=[
            {"role": "腹", "left_safe": 10, "right_safe": 20},
            {"role": "翼", "left_safe": 100, "right_safe": 200},
        ])],
    })

    enhanced = enhance_bh_projection(projection, contract)

    assert enhanced.status == "complete"
    assert enhanced.matched_occurrence_count == 2
    assert enhanced.missing_drawing_count == 0
    assert enhanced.unmatched_drawing_count == 0
    bh_rows = [
        row for row in enhanced.projection.organized_rows
        if row["类型"] in {"BH腹", "BH翼"}
    ]
    assert [
        (
            row["构件编号"],
            row["导入零件号"],
            row["长度(mm)"],
            row["左进(mm)"],
            row["右进(mm)"],
            row["下料长度(mm)"],
            row["数量"],
            row["总数"],
        )
        for row in bh_rows
    ] == [
        ("C1", "P-1-BH腹", Decimal("1000"), Decimal("10"), Decimal("20"), Decimal("970"), Decimal("2"), Decimal("4")),
        ("C1", "P-1-BH翼", Decimal("1000"), Decimal("100"), Decimal("200"), Decimal("700"), Decimal("4"), Decimal("8")),
        ("C2", "P-1-BH腹", Decimal("1200"), Decimal("10"), Decimal("20"), Decimal("1170"), Decimal("2"), Decimal("6")),
        ("C2", "P-1-BH翼", Decimal("1200"), Decimal("100"), Decimal("200"), Decimal("900"), Decimal("4"), Decimal("12")),
    ]
    part_result = build_part_rows(enhanced.projection.part_candidates)
    assert part_result.issues == ()
    assert [
        (row.import_component_no, row.import_part_no, row.cut_length, row.summary)
        for row in part_result.rows
    ] == [
        ("C1", "P-1-BH腹", Decimal("970"), Decimal("4")),
        ("C1", "P-1-BH翼", Decimal("700"), Decimal("8")),
        ("C2", "P-1-BH腹", Decimal("1170"), Decimal("6")),
        ("C2", "P-1-BH翼", Decimal("900"), Decimal("12")),
    ]


def test_missing_drawing_keeps_stage1_length_and_adds_nonisolating_warning() -> None:
    projection = _projection(
        _source_part(source_row=8, component_no="C1", component_qty="2"),
    )
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [],
    })

    enhanced = enhance_bh_projection(projection, contract)

    assert enhanced.status == "partial"
    assert enhanced.missing_drawing_count == 1
    rows = enhanced.projection.organized_rows
    assert len(rows) == 2
    assert all(row["左进(mm)"] is None and row["右进(mm)"] is None for row in rows)
    assert all(row["下料长度(mm)"] == Decimal("1000") for row in rows)
    assert all(row["_stage2_status"] == "missing" for row in rows)
    assert all(row["_stage2_issue_category"] == "BH缺图沿用原长度" for row in rows)
    new_issues = enhanced.projection.issues[len(projection.issues):]
    assert len(new_issues) == 1
    assert new_issues[0].category == "BH缺图沿用原长度"
    assert new_issues[0].level.value == "警告"
    assert new_issues[0].affects_part is False
    assert enhanced.projection.part_candidates == projection.part_candidates


def test_reader_failure_keeps_red_placeholders_and_never_treats_unknown_as_zero() -> None:
    projection = _projection(
        _source_part(source_row=8, component_no="C1", component_qty="2"),
    )
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item(
            status="ERROR_UNHANDLED",
            reader_spec="",
            measurements=[],
            warnings=["DXF无法读取"],
        )],
    })

    enhanced = enhance_bh_projection(projection, contract)

    assert enhanced.status == "partial"
    assert enhanced.missing_drawing_count == 0
    rows = enhanced.projection.organized_rows
    assert len(rows) == 2
    assert [row["类型"] for row in rows] == ["BH腹", "BH翼"]
    assert [row["数量"] for row in rows] == [Decimal("2"), Decimal("4")]
    assert [row["总数"] for row in rows] == [Decimal("4"), Decimal("8")]
    for row in rows:
        assert row["_stage2_status"] == "manual"
        assert row["_stage2_issue_category"] == "BH读取失败需补录"
        assert row["左进(mm)"] is None
        assert row["右进(mm)"] is None
        assert row["下料长度(mm)"] is None
        assert row["总长(mm)"] is None
        assert row["理单重(kg)"] is None
        assert row["理总重(kg)"] is None
    assert rows[0]["单净重(kg)"] == Decimal("12")
    assert rows[0]["单毛重(kg)"] == Decimal("13")
    assert rows[0]["单表面积(㎡)"] == Decimal("1.5")
    assert rows[1]["单净重(kg)"] is None
    assert rows[1]["单毛重(kg)"] is None
    assert rows[1]["单表面积(㎡)"] is None

    candidates = enhanced.projection.part_candidates
    assert len(candidates) == 2
    assert all(candidate.cut_length is None for candidate in candidates)
    assert all(candidate.model_length == Decimal("1000") for candidate in candidates)
    assert all(candidate.excluded is False for candidate in candidates)
    part_result = build_part_rows(candidates)
    assert part_result.issues == ()
    assert [row.cut_length for row in part_result.rows] == [None, None]
    assert [row.summary for row in part_result.rows] == [Decimal("4"), Decimal("8")]

    new_issues = enhanced.projection.issues[len(projection.issues):]
    assert len(new_issues) == 1
    assert new_issues[0].category == "BH读取失败需补录"
    assert new_issues[0].affects_part is False


def test_reader_drawing_without_excel_part_is_reported_without_inventing_rows() -> None:
    projection = _projection()
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item(
            source_file_id=321,
            file_name="orphan.dxf",
            part_number="P-ORPHAN",
        )],
    })

    enhanced = enhance_bh_projection(projection, contract)

    assert enhanced.status == "partial"
    assert enhanced.unmatched_drawing_count == 1
    assert enhanced.projection.organized_rows == ()
    assert enhanced.projection.part_candidates == ()
    new_issues = enhanced.projection.issues[len(projection.issues):]
    assert len(new_issues) == 1
    assert new_issues[0].category == "BH图纸未进入Excel"
    assert new_issues[0].source_sheet == "分类账"
    assert new_issues[0].source_row == 321
    assert new_issues[0].part_no == "P-ORPHAN"
    assert "orphan.dxf" in new_issues[0].description


def test_no_bh_on_either_side_is_an_exact_noop() -> None:
    projection = _projection()
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [],
    })

    enhanced = enhance_bh_projection(projection, contract)

    assert enhanced.status == "noop"
    assert enhanced.projection == projection


@pytest.mark.parametrize(
    "item_overrides",
    [
        {"classification_spec": "BH600*300*10*16"},
        {"reader_spec": "BH600*300*10*16"},
        {
            "measurements": [
                {"role": "腹", "left_safe": 0, "right_safe": 0},
                {"role": "上翼", "left_safe": 0, "right_safe": 0},
            ],
        },
        {
            "measurements": [
                {"role": "腹", "left_safe": -1, "right_safe": 0},
                {"role": "翼", "left_safe": 0, "right_safe": 0},
            ],
        },
    ],
)
def test_spec_role_or_setback_mismatch_becomes_manual_placeholder(
    item_overrides: dict[str, object],
) -> None:
    projection = _projection(
        _source_part(source_row=8, component_no="C1", component_qty="2"),
    )
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item(**item_overrides)],
    })

    enhanced = enhance_bh_projection(projection, contract)

    assert enhanced.status == "partial"
    assert enhanced.manual_occurrence_count == 1
    assert all(
        row["_stage2_status"] == "manual"
        and row["下料长度(mm)"] is None
        for row in enhanced.projection.organized_rows
    )
    assert enhanced.projection.issues[-1].category == "BH读取失败需补录"


def test_complete_bh_quantity_source_weight_and_theory_reduction_are_physical() -> None:
    source = _source_part(source_row=8, component_no="C1", component_qty="2")
    projection = _projection(source)
    contract = parse_bh_measurement_contract({
        "schema": "bh_setback_measurements/v1",
        "items": [_contract_item(measurements=[
            {"role": "腹", "left_safe": 10, "right_safe": 20},
            {"role": "翼", "left_safe": 100, "right_safe": 200},
        ])],
    })

    enhanced = enhance_bh_projection(projection, contract)
    web, flange = enhanced.projection.organized_rows

    assert web["数量"] + flange["数量"] == Decimal("6")
    assert web["数量"] + flange["数量"] == (
        source.original_qty + source.original_qty * Decimal("2")
    )
    for field in (
        "单净重(kg)",
        "总净重(kg)",
        "单毛重(kg)",
        "总毛重(kg)",
        "单表面积(㎡)",
        "总表面积(㎡)",
    ):
        assert web[field] is not None
        assert flange[field] is None

    baseline_total = sum(
        (row["理总重(kg)"] for row in projection.organized_rows),
        start=Decimal("0"),
    )
    enhanced_total = web["理总重(kg)"] + flange["理总重(kg)"]
    expected_reduction = (
        Decimal("10") * Decimal("468") * Decimal("30") * Decimal("4")
        + Decimal("16") * Decimal("300") * Decimal("300") * Decimal("8")
    ) * Decimal("7.85") / Decimal("1000000")
    assert enhanced_total < baseline_total
    assert abs((baseline_total - enhanced_total) - expected_reduction) <= Decimal("0.002")


def test_stage2_manual_report_categories_are_aggregated_across_specs() -> None:
    projection = _projection()
    items = [
        _contract_item(
            source_file_id=400 + index,
            file_name=f"orphan-{index}.dxf",
            part_number=f"P-{index}",
            classification_spec=f"BH{500 + index}*300*10*16",
            reader_spec=f"BH{500 + index}*300*10*16",
        )
        for index in range(4)
    ]
    enhanced = enhance_bh_projection(
        projection,
        parse_bh_measurement_contract({
            "schema": "bh_setback_measurements/v1",
            "items": items,
        }),
    )
    ledger = QualityLedger()
    for issue in enhanced.projection.issues:
        ledger.add(issue)

    report = ledger.report_rows()

    assert len(report) == 1
    assert report[0]["类别"] == "BH图纸未进入Excel"
    assert "影响 4 行" in report[0]["说明"]
    assert "另有 1 种说明" in report[0]["说明"]


def test_enhancement_replaces_bh_in_place_and_preserves_non_bh_candidates() -> None:
    before = _projection(
        _source_part(
            source_row=7,
            component_no="C0",
            component_qty="1",
            part_no="PL-1",
            original_spec="PL10*100",
        ),
        _source_part(source_row=8, component_no="C1", component_qty="2"),
        _source_part(
            source_row=9,
            component_no="C2",
            component_qty="1",
            part_no="PL-2",
            original_spec="PL12*120",
        ),
    )
    non_bh_before = tuple(
        candidate
        for candidate in before.part_candidates
        if candidate.part_type not in {"BH腹", "BH翼"}
    )
    enhanced = enhance_bh_projection(
        before,
        parse_bh_measurement_contract({
            "schema": "bh_setback_measurements/v1",
            "items": [_contract_item()],
        }),
    )

    assert enhanced.projection.cleaned_parts is before.cleaned_parts
    assert enhanced.projection.component_rows is before.component_rows
    assert [
        candidate.source_row for candidate in enhanced.projection.part_candidates
    ] == [7, 8, 8, 9]
    non_bh_after = tuple(
        candidate
        for candidate in enhanced.projection.part_candidates
        if candidate.part_type not in {"BH腹", "BH翼"}
    )
    assert all(
        after is original
        for after, original in zip(non_bh_after, non_bh_before, strict=True)
    )


def test_missing_bh_part_baseline_is_a_blocking_projection_error() -> None:
    projection = _projection(
        _source_part(source_row=8, component_no="C1", component_qty="2"),
    )
    broken = replace(
        projection,
        part_candidates=projection.part_candidates[:1],
    )

    with pytest.raises(ValueError, match="part候选基线"):
        enhance_bh_projection(
            broken,
            parse_bh_measurement_contract({
                "schema": "bh_setback_measurements/v1",
                "items": [_contract_item()],
            }),
        )


def test_missing_clean_source_for_bh_baseline_has_a_stable_error() -> None:
    projection = _projection(
        _source_part(source_row=8, component_no="C1", component_qty="2"),
    )
    broken = replace(projection, cleaned_parts=())

    with pytest.raises(ValueError, match="BH 基线来源不存在于清洗表"):
        enhance_bh_projection(
            broken,
            parse_bh_measurement_contract({
                "schema": "bh_setback_measurements/v1",
                "items": [_contract_item()],
            }),
        )


@pytest.mark.parametrize(
    ("role", "part_type", "import_part_no", "quantity_multiplier", "sort_key"),
    [
        ("腹", "BH腹", "P-1-BH腹", Decimal("1"), (0, 0, 0)),
        ("翼", "BH翼", "P-1-BH翼", Decimal("2"), (1, 0, 0)),
        ("翼-2", "BH翼", "P-1-BH翼-2", Decimal("2"), (1, 0, 2)),
        ("上翼", "BH翼", "P-1-BH上翼", Decimal("1"), (1, 1, 0)),
        ("下翼", "BH翼", "P-1-BH下翼", Decimal("1"), (1, 2, 0)),
        ("上翼-3", "BH翼", "P-1-BH上翼-3", Decimal("1"), (1, 1, 3)),
        ("下翼-4", "BH翼", "P-1-BH下翼-4", Decimal("1"), (1, 2, 4)),
    ],
)
def test_reader_role_mapping_is_explicit_and_stable(
    role: str,
    part_type: str,
    import_part_no: str,
    quantity_multiplier: Decimal,
    sort_key: tuple[int, int, int],
) -> None:
    mapped = map_bh_role("P-1", role)

    assert mapped.part_type == part_type
    assert mapped.import_part_no == import_part_no
    assert mapped.quantity_multiplier == quantity_multiplier
    assert mapped.sort_key == sort_key


@pytest.mark.parametrize("role", ["", "翼-0", "翼-X", "左翼", "腹-1"])
def test_reader_role_mapping_rejects_unknown_or_nonpositive_roles(role: str) -> None:
    with pytest.raises(ValueError, match="BH Reader 角色"):
        map_bh_role("P-1", role)
